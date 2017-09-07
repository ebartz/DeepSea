# -*- coding: utf-8 -*-

# fs.py
# ------------------------------------------------------------------------------
#
# Module for performing filesystem operations.
#

import logging
import os
import psutil
import tempfile
import shutil
import pprint
from subprocess import Popen, PIPE

log = logging.getLogger(__name__)

# General helper functions
def _run(cmd):
    """
    NOTE: Taken from osd.py module.
    """
    log.info(cmd)
    proc = Popen(cmd, stdout=PIPE, stderr=PIPE, shell=True)
    proc.wait()
    _stdout = proc.stdout.read().rstrip()
    _stderr = proc.stdout.read().rstrip()
    log.debug("return code: {}".format(proc.returncode))
    log.debug(_stdout)
    log.debug(_stderr)
    log.debug(pprint.pformat(proc.stdout.read()))
    log.debug(pprint.pformat(proc.stderr.read()))
    #return proc.returncode, _stdout, _stderr
    return proc.returncode, _stdout, _stderr

# Device functions
def _uuid_device(device, pathname="/dev/disk/by-uuid"):
    """
    Return the uuid device

    NOTE: Simplified form from osd.py
    """
    cmd = "find -L {} -samefile {}".format(pathname, device)
    rc, _stdout, _stderr = _run(cmd)
    if _stdout:
	return os.path.basename(_stdout)
    else:
	return None

def _get_device_of_partition(partition):
    """
    Remove trailing numbers of partition, and for nvme, remove trailing 'p' as well.
    """
    partition = partition.rstrip("1234567890")
    # For nvme, strip the trailing 'p' as well.
    if "nvme" in partition:
	partition = partition[:-1]

    return partition


def _inspect_device(mount_point):
    """
    Inspect the underlying device of the given mount_point.

    Returns dev_dict or None on error.

    dev_d: {
      path:   String,      # Device path (ie. /dev/sdb2)
      uuid:   String,      # UUID of device path
      ssd:    Bool/None    # True if device is an SSD, False if HDD None if unknown.
    }
    """
    dev_d = {'path': "", 'uuid': "", 'ssd': None}
    device = None

    for part in psutil.disk_partitions():
	if part.mountpoint == mount_point:
	    device = part.device

    if not device:
	log.error("Failed to find device of mount point {}.".format(mount_point))
	return None
    else:
	dev_d['path'] = device

    # Check if we're on an SSD or not.
    try:
	with open("/sys/block/{}/queue/rotational".format(os.path.basename(_get_device_of_partition(device))), 'r') as f:
            # ssd can be True, False, or None (unknown)
            line = f.readline().rstrip()
            if line == '0':
                dev_d['ssd'] = True
            elif line == '1':
                dev_d['ssd'] = False
    except:
	# For some reason, the file doesn't exist or we can't open it.
	log.error("Failed to determine if {} is a solid state device.".format(_get_device_of_partition(device)))
	return None

    dev_d['uuid'] = _uuid_device(device)
    return dev_d

# FS functions
def _inspect_fs(mount_point):
    """
    Inspect the underlying filesystem of the given mount_point.

    Returns fs_d or None on error.

    fs_d: {
      type:   String,      # xfs, btrfs, ext4, etc
      ...                  # Other fs related keys
    }
    """
    fs_d = { 'type': "" }
    fstype = None

    for part in psutil.disk_partitions():
	if part.mountpoint == mount_point:
	    fstype = part.fstype

    if not fstype:
	log.error("Failed to find fs type of mount point {}.".format(mount_point))
        return None
    else:
	fs_d['type'] = fstype

    # TODO: populate additional stuff?

    return fs_d

# Mount point functions
def _get_mount_point(path):
    """
    Check if path is a mount point.  If not, split the path until either a mount
    point is found, or path is empty.
    """
    if not path:
	return None
    if path and os.path.ismount(path):
	return path
    else:
	return _get_mount_point(os.path.split(path)[0])

def _get_mount_opts(mount_point):
    """
    Return an array of mount option strings and/or key:value dictionaries for the
    given mount_point.
    """
    opts = []

    for part in psutil.disk_partitions():
	if part.mountpoint == mount_point:
	    opts = part.opts.split(',')

    # Convert foo=bar to dictionary entries
    # for i in range(len(opts)):
    #     opts[i] = {k:v for (k,v) in [tuple(opts[i].split('='))]} if '=' in opts[i] else opts[i]
    opts = [ o if '=' not in o else {k:v for (k,v) in [tuple(o.split('='))]} for o in opts ]

    return opts

def get_mount_point(path = None, **kwargs):
    """
    Return the mountpoint of path or kwargs['path'], or None if not found.
    NOTE: We could take the abspath of kwargs['path'], but we can
    just rely on hitting an the empty string case in
    _get_mount_point.
    """
    if not path:
	path = kwargs['path'] if kwargs.has_key('path') else ""

    return _get_mount_point(path)

def inspect_mount_point(path = None, **kwargs):
    """
    Inspect the path's mount point (or potential mount point if the path does
    not yet exist.

    Returns mount_d or None on error.

    mount_d: {
      path:   String,                # Mount point of the path (ie. / )
      opts:   [ String | { k:v } ],  # Mount opts
      dev_d:  dev_d,                 # Device level dictionary
      fs_d:   fs_d                   # FS level dictionary
    }
    """
    if not path:
	path = kwargs['path'] if kwargs.has_key('path') else ""

    mount_d = { 'path': "", 'opts': [], 'dev_d': None, 'fs_d': None }

    # Get the mount point of path.
    mount_point_path = get_mount_point(path)
    if not mount_point_path:
	log.error("Failed to get mount point path of {}.".format(path))
	return None
    else:
	mount_d['path'] = mount_point_path

    # From here on out, we can be confident that mount_point_path is valid.

    # Get mount options.
    # TODO: Should empty mount opts really cause a failure here?
    opts = _get_mount_opts(mount_point_path)
    if not opts:
	log.error("Failed to get mount point options of {}.".format(mount_point_path))
	return None
    else:
	mount_d['opts'] = opts

    dev_d = _inspect_device(mount_point_path)
    if not dev_d:
	log.error("Failed to get device information of mount point {}.".format(mount_point_path))
	return None
    else:
	mount_d['dev_d'] = dev_d

    fs_d = _inspect_fs(mount_point_path)
    if not fs_d:
	log.error("Failed to get filesystem information of mount point {}.".format(mount_point_path))
	return None
    else:
	mount_d['fs_d'] = fs_d

    return mount_d

def exists(path = None, **kwargs):
    """
    Returns True if path exists on this minion, False otherwise.
    """
    if not path:
	path = kwargs['path'] if kwargs.has_key('path') else ""

    return os.path.exists(path)

# Main inspection function

def is_cow(path = None, **kwargs):
    """
    Check if CoW is set for a given path, and return True/False.
    Returns None if the path doesn't exist.
    """
    if not path:
	path = kwargs['path'] if kwargs.has_key('path') else ""

    if not exists(path):
	return None

    cmd = "lsattr -dl {} | grep -i No_COW".format(path)
    rc, _stdout, _stderr = _run(cmd)

    return False if rc == 0 else True

def inspect_path(**kwargs):
    """
    Inspect path on the given minion.

    Returns path_d or None.

    path_d:  {
      ret:     Bool,        # Whether the full inspection succeeded
      exists:  Bool,        # We want to handle paths that don't yet exist
      cow:     Bool,        # Is path CoW
      mount_d: mount_dict,  # Mount point level dictionary
    }
    """
    path_d = { 'ret': True, 'exists': False, 'cow': None, 'mount_d': None }

    path = kwargs['path'] if kwargs.has_key('path') else ""
    if not path:
	log.error("Path not supplied for inspection.")
	return None

    # Check if path exists.
    path_d['exists'] = exists(path)

    # Check CoW.
    path_d['cow'] = is_cow(path)

    # Obtain mount information about path.
    # NOTE: Yes, the granularity of failure is a bit brutal.  We may have some
    #       valid data, but one of the inspection functions has failed, so
    #       let's not trust any mount/device/fs data, and just fail for this
    #       node.
    mount_d = inspect_mount_point(path)
    if not mount_d:
	log.error("Failed to obtain mount point info from {}.".format(path))
	path_d['ret'] = False
    else:
	path_d['mount_d'] = mount_d

    return path_d
