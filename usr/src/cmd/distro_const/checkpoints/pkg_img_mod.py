#!/usr/bin/python
#
# CDDL HEADER START
#
# The contents of this file are subject to the terms of the
# Common Development and Distribution License (the "License").
# You may not use this file except in compliance with the License.
#
# You can obtain a copy of the license at usr/src/OPENSOLARIS.LICENSE
# or http://www.opensolaris.org/os/licensing.
# See the License for the specific language governing permissions
# and limitations under the License.
#
# When distributing Covered Code, include this CDDL HEADER in each
# file and include the License file at usr/src/OPENSOLARIS.LICENSE.
# If applicable, add the following below this CDDL HEADER, with the
# fields enclosed by brackets "[]" replaced with your own identifying
# information: Portions Copyright [yyyy] [name of copyright owner]
#
# CDDL HEADER END
#

#
# Copyright (c) 2010, 2011, Oracle and/or its affiliates. All rights reserved.
#

""" pkg_img_mod

 Customizations to the package image area after the boot archive
 has been created

"""
import os
import platform
import shutil

from osol_install.install_utils import dir_size, file_size
from solaris_install import CalledProcessError, DC_LABEL, Popen, run
from solaris_install.data_object.data_dict import DataObjectDict
from solaris_install.engine import InstallEngine
from solaris_install.engine.checkpoint import AbstractCheckpoint as Checkpoint
from solaris_install.transfer.info import Software, Source, Destination, \
    CPIOSpec, Dir
from solaris_install.transfer.media_transfer import TRANSFER_MEDIA, \
    INSTALL_TARGET_VAR, MEDIA_DIR_VAR, TRANSFER_MANIFEST_NAME, \
    TRANSFER_MISC
from solaris_install.manifest.writer import ManifestWriter

# load a table of common unix cli calls
import solaris_install.distro_const.cli as cli
cli = cli.CLI()


class PkgImgMod(Checkpoint):
    """ PkgImgMod - class to modify the pkg_image directory after the boot
    archive is built.
    """

    DEFAULT_ARG = {"compression_type": "gzip"}
    VALID_COMPRESSION = ["gzip", "lzma"]

    def __init__(self, name, arg=DEFAULT_ARG):
        super(PkgImgMod, self).__init__(name)
        self.compression_type = arg.get("compression_type",
            self.DEFAULT_ARG.get("compression_type"))

        if self.compression_type not in self.VALID_COMPRESSION:
            raise RuntimeError("invalid compression_type:  " +
                               self.compression_type)

        self.dist_iso_sort = arg.get("dist_iso_sort")

        # instance attributes
        self.doc = None
        self.dc_dict = {}
        self.pkg_img_path = None
        self.ba_build = None
        self.tmp_dir = None

    def get_progress_estimate(self):
        """Returns an estimate of the time this checkpoint will take"""
        return 415

    def parse_doc(self):
        """ class method for parsing data object cache (DOC) objects for use by
        the checkpoint.
        """
        self.doc = InstallEngine.get_instance().data_object_cache
        self.dc_dict = self.doc.volatile.get_children(name=DC_LABEL,
            class_type=DataObjectDict)[0].data_dict

        try:
            self.pkg_img_path = self.dc_dict["pkg_img_path"]
            self.tmp_dir = self.dc_dict["tmp_dir"]
            self.ba_build = self.dc_dict["ba_build"]
        except KeyError, msg:
            raise RuntimeError("Error retrieving a value from the DOC: " +
                                str(msg))

    def strip_root(self):
        """ class method to clean up the root of the package image path
        """
        if not os.path.isdir(self.pkg_img_path):
            raise RuntimeError("Package Image path " + self.pkg_img_path +
                            " is not valid")

        # Copy the volsetid to the root of the image
        shutil.copy(os.path.join(self.ba_build, ".volsetid"),
                    self.pkg_img_path)

        # Remove the password lock file left around from user actions
        # during package installation; if left in place it becomes a
        # symlink into /mnt/misc which causes installer's attempt to
        # create a user account to fail
        if os.path.exists(os.path.join(self.pkg_img_path,
                                       "etc/.pwd.lock")):
            os.remove(self.pkg_img_path + "/etc/.pwd.lock")

        os.chdir(self.pkg_img_path)

        # sbin, kernel and lib are contained within the boot_archive
        # Thus, not needed in the pkg_image area
        self.logger.info("Removing sbin, kernel and lib from " +
                         "pkg_image area")
        shutil.rmtree("sbin", ignore_errors=True)
        shutil.rmtree("kernel", ignore_errors=True)
        shutil.rmtree("lib", ignore_errors=True)

    def strip_x86_platform(self):
        """ class method to clean up the package image path for x86 systems
        """
        # save the current working directory
        cwd = os.getcwd()

        os.chdir(os.path.join(self.pkg_img_path, "platform"))
        # walk the directory tree and remove anything other than the kernel
        # and boot_archive files
        for (root, _none, files) in os.walk("."):
            for f in files:
                if f == "unix" or f == "boot_archive":
                    continue
                else:
                    self.logger.debug("removing " + os.path.join(root, f))
                    os.unlink(os.path.join(root, f))

        # copy the platform directory to /boot since grub does not understand
        # symlinks
        os.chdir(self.pkg_img_path)
        shutil.copytree(os.path.join(self.pkg_img_path, "platform"),
                        os.path.join(self.pkg_img_path, "boot/platform"),
                        symlinks=True)

        os.chdir(cwd)

    def strip_sparc_platform(self):
        """ class method to clean up the package image path for sparc systems
        """
        os.chdir(os.path.join(self.pkg_img_path, "platform"))
        # walk the directory tree and remove anything other than wanboot
        # and boot_archive files
        for (root, _none, files) in os.walk("."):
            for f in files:
                if f == "wanboot" or f == "boot_archive":
                    continue
                else:
                    self.logger.debug("removing " + os.path.join(root, f))
                    os.unlink(os.path.join(root, f))

        # symlink the platform directory in boot:
        # boot/platform -> ../platform
        os.chdir(self.pkg_img_path)
        os.symlink(os.path.join("..", "platform"),
                   os.path.join(self.pkg_img_path, "boot/platform"))

    def create_usr_archive(self):
        """ class method to create the /usr file system archive
        """
        os.chdir(self.pkg_img_path)

        # Generate the /usr file system archive.
        self.logger.info("Generating /usr file system archive")

        cmd = [cli.MKISOFS, "-o", "solaris.zlib", "-quiet", "-N",
               "-l", "-R", "-U", "-allow-multidot", "-no-iso-translate",
               "-cache-inodes", "-d", "-D", "-V", "\"compress\"", "usr"]

        # Use the iso_sort file if one is specified
        if self.dist_iso_sort is not None and \
           os.path.exists(self.dist_iso_sort):
            # insert the flags directly after the name of the output file
            cmd.insert(3, "-sort")
            cmd.insert(4, self.dist_iso_sort)
        run(cmd)

        self.logger.info("Compressing /usr file system archive using: " +
                         self.compression_type)

        cmd = [cli.LOFIADM, "-C", self.compression_type,
               os.path.join(self.pkg_img_path, "solaris.zlib")]
        try:
            p = run(cmd)
        except CalledProcessError:
            raise RuntimeError("Compression of /usr file system failed: " +
                               os.strerror(p.returncode))

    def create_misc_archive(self):
        """ class method to create the /mnt/misc file system archive
        """
        os.chdir(self.pkg_img_path)

        self.logger.info("Generating /mnt/misc file system archive")

        os.mkdir("miscdirs")
        shutil.move("opt", "miscdirs")
        shutil.move("etc", "miscdirs")
        shutil.move("var", "miscdirs")

        # add Software node to install items from /mnt/misc

        src_path = Dir("/mnt/misc")
        src = Source()
        src.insert_children(src_path)

        dst_path = Dir(INSTALL_TARGET_VAR)
        dst = Destination()
        dst.insert_children(dst_path)

        tr_install_misc = CPIOSpec()
        tr_install_misc.action = CPIOSpec.INSTALL
        tr_install_misc.contents = ["."]
        tr_install_misc.size = str(dir_size(os.path.join(self.pkg_img_path,
                                                         "miscdirs")))

        misc_software_node = Software(TRANSFER_MISC, type="CPIO")
        misc_software_node.insert_children([src, dst, tr_install_misc])
        self.doc.persistent.insert_children(misc_software_node)

        cmd = [cli.MKISOFS, "-o", "solarismisc.zlib", "-N", "-l", "-R",
               "-U", "-allow-multidot", "-no-iso-translate", "-quiet",
               "-cache-inodes", "-d", "-D", "-V", "\"compress\"",
               "miscdirs"]
        run(cmd)

        self.logger.info("Compressing /mnt/misc file system archive " +
                         "using: " + self.compression_type)

        cmd = [cli.LOFIADM, "-C", self.compression_type,
               os.path.join(self.pkg_img_path, "solarismisc.zlib")]
        p = run(cmd, check_result=Popen.ANY)
        if p.returncode != 0:
            if "invalid algorithm name" in p.stderr:
                raise RuntimeError("Invalid compression algorithm " +
                    "specified for /mnt/misc archive: " +
                    self.compression_type)
            else:
                raise RuntimeError("Compression of /mnt/misc file system " +
                                   "failed:  " + os.strerror(p.returncode))

        # the removal of /usr must be deferred to until solarismisc.zlib has
        # been created because the contents of solarismisc.zlib actually come
        # from /usr
        shutil.rmtree(os.path.join(self.pkg_img_path, "miscdirs"),
                      ignore_errors=True)
        shutil.rmtree(os.path.join(self.pkg_img_path, "usr"),
                      ignore_errors=True)

    def add_content_list_to_doc(self, content_list):
        src_path = Dir(MEDIA_DIR_VAR)
        src = Source()
        src.insert_children(src_path)

        dst_path = Dir(INSTALL_TARGET_VAR)
        dst = Destination()
        dst.insert_children(dst_path)

        media_install = CPIOSpec()
        media_install.action = CPIOSpec.INSTALL
        media_install.contents = content_list
        total_size_byte = 0
        for content in content_list:
            content_path = os.path.join(self.pkg_img_path, content)
            # only want to calculate the size of files, since directories
            # are traversed and it's files are included in the list.
            if not os.path.isdir(content_path):
                total_size_byte += file_size(content_path)
        media_install.size = str(total_size_byte)

        media_soft_node = Software(TRANSFER_MEDIA, type="CPIO")
        media_soft_node.insert_children([src, dst, media_install])

        # Add that into the software transfer list.
        self.doc.persistent.insert_children(media_soft_node)

        # call manifest writer to write out the content of
        # the transfer manifest
        manifest_out = os.path.join(self.pkg_img_path, TRANSFER_MANIFEST_NAME)
        xslt_name = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "xslt", "doc2_media_transfer.xslt")
        manifest_writer = ManifestWriter("manifest-writer",
                                         manifest_out, xslt_file=xslt_name)
        manifest_writer.write(self.doc)

    def populate_livecd_content(self):
        """ class method to populate content of live media's root into DOC
        """
        # save the current working directory
        cwd = os.getcwd()

        # change to the pkg_img_path
        os.chdir(self.pkg_img_path)

        content_list = []
        for root, dirs, files in os.walk("."):
            for f in files:
                if not f.endswith(".zlib") and not f.endswith(".image_info") \
                    and not f.endswith("boot_archive") and not \
                    f.endswith(".media-transfer.xml"):
                    content_list.append(os.path.join(root, f))
            for d in dirs:
                content_list.append(os.path.join(root, d))

        self.add_content_list_to_doc(content_list)

        os.chdir(cwd)

    def populate_save_list(self):
        '''Store a list of files under the 'save' directory. Net-booted
        text installer uses this list to determine what files it needs from
        the boot server
        '''
        save_files = []
        save_dir = os.path.join(self.pkg_img_path, "save")
        for root, _none, files in os.walk(save_dir):
            for f in files:
                relpath = os.path.relpath(os.path.join(root, f),
                                          start=self.pkg_img_path)
                save_files.append(relpath)

        self.add_content_list_to_doc(save_files)

    def execute(self, dry_run=False):
        """Customize the pkg_image area. Assumes that a populated pkg_image
           area exists and that the boot_archive has been built
        dry_run is not used in DC
        """
        self.logger.info("=== Executing Pkg Image Modification Checkpoint ===")

        self.parse_doc()

        # clean up the root of the package image path
        self.strip_root()

        # create the /usr archive
        self.create_usr_archive()

        # create the /mnt/misc archive
        self.create_misc_archive()


class LiveCDPkgImgMod(PkgImgMod, Checkpoint):
    """ LiveCDPkgImgMod - class to modify the pkg_image directory after the
    boot archive is built for Live CD distributions
    """

    DEFAULT_ARG = {"compression_type": "gzip"}

    def __init__(self, name, arg=DEFAULT_ARG):
        super(LiveCDPkgImgMod, self).__init__(name, arg)

    def cleanup_icons(self):
        """ class method to remove all icon-theme.cache files
        """
        self.logger.debug("Cleaning out icon theme cache")

        os.chdir(os.path.join(self.pkg_img_path, "usr"))
        for root, _none, files in os.walk("."):
            if "icon-theme.cache" in files:
                os.unlink(os.path.join(root, "icon-theme.cache"))

    def strip_platform(self):
        """ class method to remove every file from platform/ except for the
        kernel and boot_archive.
        """
        self.logger.debug("platform only needs to contain the kernel " +
                          "and boot_archive")

        os.chdir(os.path.join(self.pkg_img_path, "platform"))
        for root, _none, files in os.walk("."):
            for f in files:
                if f == "unix" or f == "boot_archive":
                    continue
                os.unlink(os.path.join(root, f))

    def execute(self, dry_run=False):
        """Customize the pkg_image area. Assumes that a populated pkg_image
           area exists and that the boot_archive has been built
        """
        self.logger.info("=== Executing Pkg Image Modification Checkpoint ===")

        self.parse_doc()

        # remove icon caches
        self.cleanup_icons()

        # clean up the root of the package image path
        self.strip_root()

        # create the /usr archive
        self.create_usr_archive()

        # create the /mnt/misc archive
        self.create_misc_archive()

        # strip the /platform directory
        self.strip_platform()

        # populate live cd's content into DOC
        self.populate_livecd_content()


class TextPkgImgMod(PkgImgMod, Checkpoint):
    """ TextPkgImgMod - class to modify the pkg_image directory after the boot
    archive is built for Text media
    """

    DEFAULT_ARG = {"compression_type": "gzip"}

    def __init__(self, name, arg=DEFAULT_ARG):
        super(TextPkgImgMod, self).__init__(name, arg)

    def execute(self, dry_run=False):
        """ Customize the pkg_image area. Assumes that a populated pkg_image
        area exists and that the boot_archive has been built
        """
        self.logger.info("=== Executing Pkg Image Modification Checkpoint ===")

        self.parse_doc()

        # clean up the root of the package image path
        self.strip_root()

        # create the /usr archive
        self.create_usr_archive()

        # create the /mnt/misc archive
        self.create_misc_archive()

        # get the platform of the system
        arch = platform.processor()

        # save the current working directory
        cwd = os.getcwd()
        try:
            # clean up the package image path based on the platform
            if arch == "i386":
                self.strip_x86_platform()
            else:
                self.strip_sparc_platform()

            # populate live cd's content into DOC
            self.populate_livecd_content()
        finally:
            # return to the initial directory
            os.chdir(cwd)


class AIPkgImgMod(TextPkgImgMod):
    """ AIPkgImgMod - class to modify the pkg_image directory after the boot
    archive is built for AI distributions
    """

    DEFAULT_ARG = {"compression_type": "gzip"}

    def __init__(self, name, arg=DEFAULT_ARG):
        super(AIPkgImgMod, self).__init__(name, arg)

    def execute(self, dry_run=False):
        """ Customize the pkg_image area. Assumes that a populated pkg_image
        area exists and that the boot_archive has been built
        """
        self.logger.info("=== Executing Pkg Image Modification Checkpoint ===")

        self.parse_doc()

        # clean up the root of the package image path
        self.strip_root()

        # create the /usr archive
        self.create_usr_archive()

        # create the /mnt/misc archive
        self.create_misc_archive()

        # get the platform of the system
        arch = platform.processor()

        # save the current working directory
        cwd = os.getcwd()
        try:
            # clean up the package image path based on the platform
            if arch == "i386":
                self.strip_x86_platform()
            else:
                self.strip_sparc_platform()

            # populate the value from the save directory into the DOC
            self.populate_save_list()

        finally:
            # return to the initial directory
            os.chdir(cwd)
