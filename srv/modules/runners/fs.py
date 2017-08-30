# -*- coding: utf-8 -*-

import salt.client
import salt.utils.error
import logging
import pprint
import deepsea_minions

import os
import sys

log = logging.getLogger(__name__)

class Btrfs(object):
    def __init__(self):
        self.vlc = "/var/lib/ceph"
        self.target = deepsea_minions.DeepseaMinions()
        self.search = self.target.deepsea_minions
        self.local = salt.client.LocalClient()
        # Populate btfrs_minions on init.
        self.btrfs_minions = []
        self.fs_get_btrfs_minions()

    def fs_get_btrfs_minions(self):
        """
        Populate instance list of minions that have 'path' (or it's parent mount point) mounted
        as btrfs.
        """
        self.btrfs_minions = []
        for minion, fs_type in self.local.cmd(self.search, 'fs.fs_get_path_fstype',
                                              ["path={}".format(self.vlc)],
                                              expr_form='compound').items():
            print "KM: minion: {} fs_type: {}".format(minion, fs_type)
            if fs_type == "btrfs":
                self.btrfs_minions.append(minion)

    def fs_create_btrfs_subvols(self):
        """
        Put /var/lib/ceph on separate btrs subvolume.  We want to do this only if
        /var/lib/ceph does not yet exist (ie. before Ceph is installed) and
        the minion has /var/lib (or it's parent mount point) mounted as btrfs.
        """
        # Dictionary of minion keys on which we'll create a /var/lib/ceph subvol.
        # They may differ from self.btrfs_minions as we will only create a subvol
        # when /var/lib/ceph is not yet present.  Value for each key is the
        # success/failure of subvol creation.
        minions = {}

        # From self.btrfs_minions, populate a list of minions that don't already have
        # /var/lib/ceph present.  These are the minions on which we'll operate.
        for minion in self.btrfs_minions:
            if not self.local.cmd(minion, 'fs.fs_path_exists',
                                  ["path={}".format(self.vlc)],
                                  expr_form='compound')[minion]:
                minions[minion] = None

        for minion in minions:
            minions[minion] = self.local.cmd(minion, 'fs.fs_btrfs_create_subvol',
                                             ["path={}".format(self.vlc)],
                                             expr_form='compound')[minion]

        print "KM: minions: {}".format(minions)

        # TODO based on minions stats, return True/False.
        return True

def fs_pre_ceph_inspect(**kwargs):
    """
    Drive any fs manipulation needed prior to installing Ceph.
    """
    ret = True
    print "KM: fs_pre_ceph_inspect()"

    # --------------------------------- BTRFS ----------------------------------
    # Check and create /var/lib/ceph btrfs subvols as needed.
    btrfs = Btrfs()
    ret = btrfs.fs_create_btrfs_subvols() if ret else False

    # ---------------------------------- XFS -----------------------------------
    # TBD

    return ret
