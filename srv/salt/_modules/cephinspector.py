# -*- coding: utf-8 -*-
# vim: ts=8 et sw=4 sts=4

import os
import socket
from subprocess import Popen, PIPE
import json
import psutil
import logging

log = logging.getLogger(__name__)

def _get_listening_ipaddrs(proc_name):
    """
    Search for proc_name running on the node and return a list of unique IPs on which proc_name
    listens.  Otherwise, return [].
    """
    proc_listening_ips = []

    for proc in psutil.process_iter():
	# Note that depending on psutil version, there are slight api differences, hence the
	# try/except blocks below.
	try:
	    name = proc.name()
	except:
	    name = proc.name
	if name == proc_name:
	    try:
		conns = proc.get_connections(kind="inet")
	    except:
		conns = proc.connections(kind="inet")
	    for con in conns:
		if con.status == "LISTEN":
		    proc_listening_ips.append(con.laddr[0])

    return list(set(proc_listening_ips))

def get_minion_public_networks():
    """
    For a given node, returns the list of unique IPs on which the ceph-mon processes are listening.
    If ceph-mon is not running/listening, returns [].
    """
    return _get_listening_ipaddrs("ceph-mon")

def get_minion_cluster_networks():
    """
    For a given node, returns the list of unique IPs on which the ceph-osd processes are listening.
    If ceph-osd is not running/listening, returns [].
    """
    return _get_listening_ipaddrs("ceph-osd")

def _get_device_of_partition(partition):
    """
    Remove trailing numbers of partition, and for nvme, remove trailing 'p' as well.
    """
    partition = partition.rstrip("1234567890")
    # For nvme, strip the trailing 'p' as well.
    if "nvme" in partition:
        partition = partition[:-1]

    return partition

def _get_disk_id(partition):
    """
    Return the disk id of a partition/device, or the original partition/device if
    the disk id is not available.
    """
    disk_id_cmd = Popen("find -L /dev/disk/by-id -samefile " + partition + " \( -name ata* -o -name nvme* \)", stdout=PIPE, stderr=PIPE, shell=True)
    out, err = disk_id_cmd.communicate()

    # We should only ever have one entry that we return.
    if out:
        return out.rstrip()
    else:
        return partition

def _get_osd_type(part_dict):
    """
    Return the OSD type string obtained from /var/lib/ceph/osd/ceph-X/type, or possibly
    None.

    TODO: Long term we should think about refactoring osd.py to expose some of these
    for use in other modules.
    """
    osd_type = None
    type_file_path = part_dict["mount"] + "/type" if part_dict.has_key("mount") else ""

    if os.path.exists(type_file_path):
        with open(type_file_path, 'r') as type_file:
            osd_type = type_file.read().rstrip()

    return osd_type

def _append_to_ceph_disk(ceph_disks, partition, part_dict):
    try:
        ceph_disks["ceph"]["storage"]["osds"][partition]
    except KeyError, e:
        ceph_disks["ceph"]["storage"]["osds"][partition] = {}
    finally:
        for k in part_dict:
            ceph_disks["ceph"]["storage"]["osds"][partition][k] = part_dict[k]

def _convert_size_to_human_readable_format(size):
    """
    Converts bytes to human readable string.  Note that precision was deliberately
    kept to the nearest whole unit per osd.py.
    TODO: another candidate for an osd.py lib fxn :)
    """
    suffixes = ['B', 'K', 'M', 'G', 'T']
    s_index = 0

    while size >= 1024 and s_index < len(suffixes):
        s_index += 1
        size = size / 1024.0

    dec = size % 1
    if dec:
        s_index -= 1
        dec = int(dec * 1024)
        size = (int(size) * 1024) + dec
    else:
        size = int(size)

    return "{}{}".format(size, suffixes[s_index])

def _get_partition_size(partition):
    """
    Returns partition size in a human readable format.
    """
    blockdev_cmd = Popen("blockdev --getsize64 {}".format(partition), stdout=PIPE, stderr=PIPE, shell=True)
    size, err = blockdev_cmd.communicate()

    try:
        size = _convert_size_to_human_readable_format(int(size))
    except ValueError, e:
        size = "0B"

    return size
    
def _append_bs_to_ceph_disk(ceph_disks, path, part_dict):
    """
    Append a bluestore OSD to ceph_disks dict.
    """
    # Make sure path is a device path, not a partition path.  ceph-disk returns
    # a device path, but let's not take any chances.
    osd_dev = _get_disk_id(_get_device_of_partition(path))

    # Take from part_dict the elements we need.
    bs_dict = { "format": "bluestore" }
    if part_dict.has_key("block.db_dev"):
        bs_dict["db"] = _get_disk_id(_get_device_of_partition(part_dict["block.db_dev"]))
        bs_dict["db_size"] = _get_partition_size(part_dict["block.db_dev"])
    if part_dict.has_key("block.wal_dev"):
        bs_dict["wal"] = _get_disk_id(_get_device_of_partition(part_dict["block.wal_dev"]))
        bs_dict["wal_size"] = _get_partition_size(part_dict["block.wal_dev"])

    _append_to_ceph_disk(ceph_disks, osd_dev, bs_dict)

def _append_fs_to_ceph_disk(ceph_disks, path, part_dict):
    """
    Append a filestore OSD to ceph_disks dict.
    """
    # Make sure path is a device path, not a partition path.  ceph-disk returns
    # a device path, but let's not take any chances.
    osd_dev = _get_disk_id(_get_device_of_partition(path))

    journal_partition = part_dict["journal_dev"] if part_dict.has_key("journal_dev") else ""
    journal_partition_size = _get_partition_size(journal_partition)

    journal_dev = _get_disk_id(_get_device_of_partition(journal_partition))

    # Take from part_dict the elements we need.
    fs_dict = { "format": "filestore", "journal": journal_dev, "journal_size": journal_partition_size }

    _append_to_ceph_disk(ceph_disks, osd_dev, fs_dict)

def get_ceph_disks_yml(**kwargs):
    """
    Generates yml representation of Ceph filestores on a given node.
    Returns something like: {"ceph": {"storage": {"osds": {"/dev/foo":
							    {"format": "filestore",
							     "journal": "/dev/bar"}}}}}
    """
    ceph_disk_list = Popen("ceph-disk list --format=json", stdout=PIPE, stderr=PIPE, shell=True)
    out, err = ceph_disk_list.communicate()
    ceph_disks = {"ceph":
                  {"storage":
                   {"osds": {} }}}

    # Failed `ceph-disk list`
    if err: return None

    out_list = json.loads(out)
    # [ { 'path': '/dev/foo', 'partitions': [ {...}, ... ], ... }, ... ]
    # The partitions list has all the goodies.
    for out_dict in out_list:
        # Grab the path (ie. /dev/foo).
        path = out_dict["path"] if out_dict.has_key("path") else None
        # Paranoia: check to make sure we have a path and a "partitions" entry.
        if path and out_dict.has_key("partitions"):
            for part_dict in out_dict["partitions"]:
                # We only care to process OSD "data" partitions.
                if part_dict.has_key("type") and part_dict["type"] == "data":
                    # Determine if we're dealing with filestore or bluestore.
                    osd_type = _get_osd_type(part_dict)
                    if osd_type == "filestore":
                        _append_fs_to_ceph_disk(ceph_disks, path, part_dict)
                    elif osd_type == "bluestore":
                        _append_bs_to_ceph_disk(ceph_disks, path, part_dict)
                    else:
                        log.warn("Unable to engulf OSD at {}. Unsupported type. Skipping.".format(path))

    return ceph_disks

def inspect(**kwargs):
    # This will *ONLY* work on a cluster named "ceph".  It won't help with
    # clusters with other names.

    # deliberately only looking for things ceph-deploy can deploy
    # TODO: do we need more than this?
    ceph_services = ['ceph-mon', 'ceph-osd', 'ceph-mds', 'ceph-mgr', 'ceph-radosgw']

    #
    # running_services will be something like:
    #
    # {
    #   'ceph-mon': [ 'hostname' ],
    #   'ceph-osd': [ '0', '1', '2', ... ]
    # }
    #
    running_services = {}
    for rs in __salt__['service.get_running']():
        instance = rs.split('@')
        if len(instance) == 2 and instance[0] in ceph_services:
            if not running_services.has_key(instance[0]):
                running_services[instance[0]] = []
            running_services[instance[0]].append(instance[1])

    ceph_conf = None
    try:
        with open("/etc/ceph/ceph.conf", "r") as conf:
            ceph_conf = conf.read()
    except:
        pass

    return {
        "running_services": running_services,
        "ceph_conf": ceph_conf,
        "has_admin_keyring": os.path.isfile("/etc/ceph/ceph.client.admin.keyring")
    }

def get_keyring(**kwargs):
    """
    Retrieve a keyring via `ceph auth get`.  Pass key=NAME_OF_KEY,
    e.g.: use key=client.admin to get the client admin key.

    Returns either the complete keyring (suitable for use in a ceph keyring
    file), or None if the key does not exist, or cannot be obtained.

    This needs to be run on a minion with a suitable ceph.conf and client
    admin keyring, in order for `ceph auth get` to be able to talk to the
    cluster.
    """
    if not "key" in kwargs:
        return None

    cmd = Popen("ceph auth get " + kwargs["key"], stdout=PIPE, stderr=PIPE, shell=True)
    out, err = cmd.communicate()

    return out if out else None
