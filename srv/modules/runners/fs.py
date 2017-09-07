# -*- coding: utf-8 -*-

import salt.client
import salt.utils.error
import logging
import pprint
import deepsea_minions

import os
import sys

log = logging.getLogger(__name__)

class Minion(object):
    """
    A Minion.  Nothing but a container for some path/fs related data.
    """
    def __init__(self, id, paths = {}):
	# Paths of interest
	self.id = id
	self.paths = paths

    def add_path(self, path):
	"""
	Add a Path instance to self.paths keyed on path_str.
	"""
	if path and path.full:
	    self.paths[path.full] = path
	else:
	    log.error("Failed to add Path to {}".format(self.id))

    def __str__(self):
	return self.id

class Device(object):
    """
    A device/partition (ie. /dev/sda1).
    """
    def __init__(self, d_path, uuid, ssd):
	self.d_path = d_path
	self.uuid = uuid
	self.ssd = ssd # Boolean whether ssd.  None == unknown

    def __str__(self):
	return self.d_path

    def get_device_type(self):
        """
        Returns 'ssd', 'hd', or 'unknown'.
        """
        disk_str = "unknown"
        if self.ssd:
            disk_str = "ssd"
        elif self.ssd == False:
            disk_str = "hd"

        return disk_str

class Mount(object):
    """
    A mount point.  Contains mount options, device and fs information.
    """
    def __init__(self, path, opts, dev, fstype):
	# The mount point on the fs.
	self.path = path
	# List of mount options.  Elements may be strings or dictionaries.
	self.opts = opts
	# Device()
	self.dev = dev
	# TODO: fs operations which call out to the minion.
	if fstype == 'btrfs':
	    self.fs = BTRFS(self)
	elif fstype == 'xfs':
	    self.fs = XFS(self)
	else:
	    self.fs = FS(fstype, self)

    def opts_to_string(self):
	opt_l = []
	for opt in self.opts:
	    if type(opt) == dict:
		for k,v in opt.iteritems():
		    opt_l.append("{}:{}".format(k,v))
	    else:
		opt_l.append(opt)
	return ", ".join(opt_l)

    def __str__(self):
        return self.path

class Path(object):
    """
    Our world here revolves around Paths (ie. /var/lib/ceph).  Based on a path,
    we aim to discover some basic mount and device information.
    """
    def __init__(self, full, exists, cow, mount_pt, mount_opts, dev_path, dev_uuid,
		 is_dev_ssd, fstype):
	# # Full pathname.
	self.full = full
	# # Does my path exist?
	self.exists = exists
	# # Is that path CoW
	self.cow = cow
	d = Device(dev_path, dev_uuid, is_dev_ssd)
	self.mount = Mount(mount_pt, mount_opts, d, fstype)

    def __str__(self):
        return self.full

    def details(self):
        """
        Dump some useful Path information.
        """
        return "{} mounted on: {} on device: {} ({}) using: {} with opts: [{}] and CoW: {}".format(
            self,
            self.mount.path,
            self.mount.dev,
            self.mount.dev.get_device_type(),
            self.mount.fs.type,
            self.mount.opts_to_string(),
            self.cow)

class FS(object):
    """
    FS Base class.  Subclasses are meant to provide interesting fs level operations
    to be invoked on Minions.
    """
    def __init__(self, fstype, mount):
	self.type = fstype
	# The Mount instance to which we're associated.
	self.mount = mount

    def get_subvol(self):
	"""
	Only btrfs has subvols.
	"""
	return None

class BTRFS(FS):
    """
    Our favourite.  Btrfs.
    """
    def __init__(self, mount):
	super(BTRFS, self).__init__('btrfs', mount)

    def get_subvol(self):
	"""
	Get subvolume.  Returns a subvolume string or None if no subvolume.
	"""
	for opt in self.mount.opts:
	    # Return the first (should be unique).
	    if type(opt) == dict and opt.has_key('subvol'):
		return opt['subvol']

        return None

class XFS(FS):
    """
    XFS.
    """
    def __init__(self, mount):
	super(XFS, self).__init__('xfs', mount)

def _dump_minion(m):
    print "Minion: {}".format(m.id)
    for p in m.paths:
	path_obj = m.paths[p]
	print "Path: {}".format(path_obj.full)
	print "Exists: {}".format(path_obj.exists)
	print "CoW: {}".format(path_obj.cow)
	print "Mount:"
	print "\tmount path: {}".format(path_obj.mount.path)
	print "\tmount opts: {}".format(path_obj.mount.opts)
	print "\tDevice:"
	print "\t\tdevice path: {}".format(path_obj.mount.dev.d_path)
	print "\t\tdevice uuid: {}".format(path_obj.mount.dev.uuid)
	print "\t\tis ssd: {}".format(path_obj.mount.dev.ssd)
	print "\t\tFS:"
	print "\t\t\tfstype: {}".format(path_obj.mount.fs.type)

def _offer_var_ceph_suggestion(path):
    """
    Offer some useful suggestions for the admin regarding /var/lib/ceph.
    """
    sys.stdout.write("\t")
    if path.mount.fs.type == 'btrfs':
        if path.exists:
            # OK, path exists, is it a subvolume already?
            if path.mount.fs.get_subvol():
                # Already a subvolume!  Check of CoW
                if path.cow:
                    sys.stdout.write("-> {} exists as a BTRFS subvolume, however "
                                         "copy-on-write is enabled.  Run "
                                         "`salt-run fs.TODO_FIX_COW` to "
                                         "disable copy-on-write.".format(path))
                else:
                    # Copy on write disabled, all good!
                    sys.stdout.write("OK")
            else:
                # Path exists, but is not a subovlume
                sys.stdout.write("-> {} exists, but is not a BTRS subvolume.  "
                                 "Run `salt-run fs.TODO_MIGRATE` to "
                                 "migrate it to a BTRFS subvolume.".format(path))
        else:
            # Path does not yet exist.
            sys.stdout.write("-> {} does not yet exist.  "
                             "Run `salt-run fs.TODO_CREATE` to create "
                             "it as a BTRFS subvolume.".format(path))
    else:
        # Not btrfs.  Nothing to suggest.
        sys.stdout.write("OK")
    sys.stdout.write("\n")

def _inspect_minions_for_path(path_str):
    """
    Create an abstract view of the path/mount/device/fs information related to
    path_str on the minions in this node.  Returns a list of populated Minion
    instances.
    """
    target = deepsea_minions.DeepseaMinions()
    search = target.deepsea_minions
    local = salt.client.LocalClient()
    minions = []

    for minion, path_info in local.cmd(search, 'fs.inspect_path',
				       ["path={}".format(path_str)],
				       expr_form='compound').items():
	# Basic sanify check.  If we failed to get _some_ data from our module for
	# a given minion, we'll set the ret flag to False.
	if path_info and path_info['ret']:
	    #print "minion: {}, path_info: {}".format(minion, path_info)
	    # Create an empty Minion instance to be populated.
	    m = Minion(minion)
	    # Create an FS instance based on the fstype computed for the mount point of our path_str.
	    fstype = path_info['mount_d']['fs_d']['type']

	    # # Create a Path instance to be added to our newly formed Minion instance.
	    path = Path(path_str, path_info['exists'], path_info['cow'],
			path_info['mount_d']['path'], path_info['mount_d']['opts'],
			path_info['mount_d']['dev_d']['path'],
			path_info['mount_d']['dev_d']['uuid'],
			path_info['mount_d']['dev_d']['ssd'], fstype)

	    m.add_path(path)
	    minions.append(m)
	else:
	    log.error("Failed to inspect {} on {}.  Check minion logs for more details".format(path_str, minion))

    return minions

def inspect_var_ceph(**kwargs):
    """
    Runner to invoke gathering of /var/lib/ceph information across all minions.  Our goal
    is to find minions that are using btrfs, but don't have /var/lib/ceph mounted as a
    subvolume.
    """
    path_of_interest = "/var/lib/ceph"

    # Quiet by default.
    quiet = kwargs['quiet'] if kwargs.has_key('quiet') else True

    minions = _inspect_minions_for_path(path_of_interest)

    # Dump
    # for m in minions:
    #     _dump_minion(m)

    # Report and offer some suggestions
    if not quiet:
        for minion in minions:
            for path_str, path in minion.paths.iteritems():
                print "{}: {}".format(minion, path.details())
                _offer_var_ceph_suggestion(path)

    return True
