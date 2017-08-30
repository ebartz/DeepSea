# -*- coding: utf-8 -*-

import logging
import os
import psutil
import tempfile
import shutil
from subprocess import Popen, PIPE

log = logging.getLogger(__name__)

def fs_btrfs_create_subvol(path=None, **kwargs):
    """
    Create a subvolume on kwargs['path'].
    """
    ret = True

    if not path:
        path = kwargs['path'] if kwargs.has_key('path') else ""
    if not path:
        return False

    # Paranoia: let's make sure path doesn't exist.
    if os.path.exists(path):
        log.warn("Unable to create new subvolume for {} as path already exists.".format(path))
        return False

    # Get the device/partition of path's mountpoint.
    device = fs_get_path_device(path)
    log.warn("KM: fs_btrfs_create_subvol: device={}".format(device))

    # Create a unique tmp directory.
    tmp_dir = tempfile.mkdtemp()

    # Mount tmpdir.
    cmd = Popen("mount -t btrfs -o subvolid=0 '{}' '{}'".format(device, tmp_dir),
                stdout=PIPE, stderr=PIPE, shell=True)
    out, err = cmd.communicate()
    if err:
        log.warn("Failed to mount {} with subvolid=0 on {}.".format(tmp_dir, device),
                 stdout=PIPE, stderr=PIPE, shell=True)
        shutil.rmtree(tmp_dir)
        ret = False

    if ret:
        # Create the subvol.
        cmd = Popen("btrfs subvolume create '{}/@/{}'".format(tmp_dir, path),
                    stdout=PIPE, stderr=PIPE, shell=True)
        out, err = cmd.communicate()
        if err:
            log.warn("Failed to create /var/lib/ceph subvolume on {}.".format(device))
            ret = False

    if ret:
        # Create /var/lib/ceph
        try:
            os.mkdir(path)
        except:
            log.warn("Failed to create /var/lib/ceph.")
            ret = False

    if ret:
        # Finally mount /var/lib/ceph subvolume.
        cmd = Popen("mount '{}' '{}' -t btrfs -o subvol=@{}".format(device, path, path),
                    stdout=PIPE, stderr=PIPE, shell=True)
        out, err = cmd.communicate()
        if err:
            log.warn("Failed to mount /var/lib/ceph subvolume.")
            ret = False

    # TODO: modify /etc/fstab to reflect the mount, or on reboot... ;)

    # Cleanup
    if os.path.exists(tmp_dir):
        cmd = Popen("umount '{}'".format(tmp_dir),
                    stdout=PIPE, stderr=PIPE, shell=True)
        shutil.rmtree(tmp_dir)
    if not ret:
        # We failed somewhere, so take care of removing the subvolume, etc.
        # TODO: there is a bug with subvolume deletes (https://bugzilla.opensuse.org/show_bug.cgi?id=957198)
        # so no more cleanup can be dont at this point.
        pass
        
    return ret

def fs_path_exists(path=None, **kwargs):
    """
    Return true/false whether kwargs['path'] exists.
    """
    if not path:
        path = kwargs['path'] if kwargs.has_key('path') else ""

    return os.path.exists(path)

def fs_get_path_device(path=None, **kwargs):
    """
    Return /dev/fooX of kwargs['path'].  If kwargs['path'] does not exist,
    returns the device of it's mount point.  Otherwise, None.
    """
    if not path:
        path = kwargs['path'] if kwargs.has_key('path') else ""
    device = None

    # If path doesn't exist, try to find it's mount point and compute
    # the device based on that.
    path = path if os.path.exists(path) else _fs_get_path_mount_point(path)

    for part in psutil.disk_partitions():
            if os.path.normpath(part.mountpoint) == os.path.normpath(path):
                device = part.device

    return device

def _fs_get_path_mount_point(path):
    """
    Check if path is a mount point.  If not, split the path until either a mount
    point is found, or path is empty.
    """
    if not path:
        return None
    if path and os.path.ismount(path):
        return path
    else:
        return _fs_get_path_mount_point(os.path.split(path)[0])

def fs_get_path_mount_point(path=None, **kwargs):
    """
    Return the mountpoint of kwargs['path'], or None if not found.
    NOTE: We could take the abspath of kwargs['path'], but we can
    just rely on hitting an the empty string case in
    _fs_get_path_mount_point.
    """
    if not path:
        path = kwargs['path'] if kwargs.has_key('path') else ""

    log.warn("KM: path:{}".format(path))
    return _fs_get_path_mount_point(path)

def fs_get_path_fstype(path=None, **kwargs):
    """
    Return the fs type of kwargs['path'].  If kwargs['path'] is not a mount point,
    move up the tree to find the parent mountpoint.
    """
    fstype = None

    if not path:
        if not kwargs.has_key('path'):
            return None
        else:
            path = kwargs['path']

    mount_point = fs_get_path_mount_point(path)
    log.warn("KM: mount_point={}".format(mount_point))

    # Only loop if mount_point was found.
    if mount_point:
        for part in psutil.disk_partitions():
            if os.path.normpath(part.mountpoint) == os.path.normpath(mount_point):
                fstype = part.fstype

    return fstype
