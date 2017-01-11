#!/usr/bin/python2.7
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
# Copyright (c) 2011, 2012, Oracle and/or its affiliates. All rights reserved.
#
'''
Objects and functions supporting accessing and modifying AI service instances

Service Version History:

0:
    Unversioned services (all services prior to derived manifests)

1: Incompatible with prior versions (automatic upgrade)
    Services supporting derived manifests (default manifest now
    included in the manifest database, and behaves like a normal manifest)

2: Incompatible with prior versions (manual upgrade required)
    "Install Server Image Management" (ISIM) services. Configuration moved
    from SMF to /var/ai. Support for aliases.
'''
import errno
import fileinput
import logging
import os
import shutil
import socket
import sys

import osol_install.auto_install.AI_database as AIdb
import osol_install.auto_install.dhcp as dhcp
import osol_install.auto_install.grub as grub
import osol_install.auto_install.installadm_common as com
import osol_install.auto_install.service_config as config
import osol_install.libaimdns as libaimdns

from osol_install.auto_install.data_files import DataFiles, insert_SQL, \
    place_manifest, validate_file
from osol_install.auto_install.image import InstalladmImage, ImageError
from osol_install.auto_install.installadm_common import _, cli_wrap as cw
from solaris_install import Popen, CalledProcessError, force_delete

MOUNT = '/usr/sbin/mount'
UNMOUNT = '/usr/sbin/umount'
SYSTEMCONF = 'system.conf'
WANBOOTCONF = 'wanboot.conf'
NETBOOT = '/etc/netboot'
MENULST = grub.MENULST

STARTING_PORT = 46501
ENDING_PORT = socket.SOL_SOCKET  # Last socket is 65535
AI_SERVICE_DIR_PATH = com.AI_SERVICE_DIR_PATH

IMG_AI_DEFAULT_MANIFEST = "auto_install/manifest/default.xml"
OLD_IMG_AI_DEFAULT_MANIFEST = "auto_install/default.xml"
SYS_AI_DEFAULT_MANIFEST = "/usr/share/auto_install/manifest/default.xml"

DEFAULT_SPARC = 'default-sparc'
DEFAULT_I386 = 'default-i386'
DEFAULT_ARCH = [DEFAULT_SPARC, DEFAULT_I386]


class AIServiceError(StandardError):
    '''Base class for errors unique to AIService'''
    pass


class UnsupportedOperationError(AIServiceError, TypeError):
    '''Raised when the service does not support the called function'''
    pass


class UnsupportedAliasError(AIServiceError):
    '''Raised when alias creation cannot be done'''
    pass


class InvalidServiceError(AIServiceError):
    '''Raised when the service cannot be enabled due to some invalid state'''
    pass


class VersionError(AIServiceError):
    '''Raised if the service's version is not supported by
    this version of installadm

    '''
    def __init__(self, service_name=None, version=None, alt_msg=None):
        self.service_name = service_name
        self.version = version
        self.alt_msg = alt_msg

    def __str__(self):
        if self.alt_msg:
            return self.alt_msg
        else:
            return self.short_str()

    def short_str(self):
        '''Returns a one-line string message for this error'''
        return (cw(_("Service '%(name)s' is incompatible: "
                     "[version: %(version)s]") %
                     {'version': self.version, 'name': self.service_name}))


class OlderVersionError(VersionError):
    def __str__(self):
        if self.alt_msg:
            return self.alt_msg

        return (cw(_("\nService '%(name)s' cannot be modified because it was "
                     "created with an older version of installadm (service "
                     "version: %(version)s).\n") %
                     {'version': self.version, 'name': self.service_name}))


class NewerVersionError(VersionError):
    def __str__(self):
        if self.alt_msg:
            return self.alt_msg

        return cw(_("\nService '%(name)s' cannot be modified because it was "
                    "created with a newer version of installadm (service "
                    "version: %(version)s).\n") %
                    {'version': self.version, 'name': self.service_name})


class MountError(AIServiceError):
    '''Service could not be properly mounted'''
    def __init__(self, source, target, reason):
        self.source = source
        self.target = target
        self.reason = reason

    def __str__(self):
        return cw(_("\nUnable to mount '%s' at '%s': %s\n" %
                    (self.source, self.target, self.reason)))


class UnmountError(MountError):
    '''Service could not be properly unmounted'''
    def __init__(self, target, reason):
        self.target = target
        self.reason = reason

    def __str__(self):
        return cw(_("\nUnable to unmount '%s': %s\n") %
                    (self.target, self.reason))


class MultipleUnmountError(UnmountError):
    '''More than one unmount failed when unmounting a service'''
    def __init__(self, still_mounted, failures):
        self.still_mounted = still_mounted
        self.failures = failures

    def __str__(self):
        result = list()
        if self.still_mounted:
            result.append(_("Failed to unmount:"))
            result.extend(self.still_mounted)
        if self.failures:
            result.extend(self.failures)
        return "\n".join([str(r) for r in result])


def mount_enabled_services(remount=False):
    '''Mounts all services configured to be enabled. If remount is True,
    and a service is already mounted, it is first unmounted (otherwise, the
    mount is left as-is. Returns a count of how many services failed to
    mount properly, and prints to stderr as the errors are encountered.

    '''
    failures = 0
    for svc_name in config.get_all_service_names():
        if not config.is_enabled(svc_name):
            continue
        try:
            svc = AIService(svc_name)
        except VersionError as err:
            # If this installadm server doesn't understand this
            # service, do not mount it.
            print >> sys.stderr, _("\nNot mounting %s") % svc_name
            print >> sys.stderr, err
            continue
        if remount and svc.mounted():
            try:
                svc.unmount(force=True)
            except MountError as err:
                print >> sys.stderr, err
                failures += 1
                continue
        try:
            svc.mount()
        except (MountError, ImageError) as err:
            print >> sys.stderr, err
            failures += 1
            continue
    return failures


def unmount_all():
    '''Unmounts all services'''
    failures = 0
    for svc_name in config.get_all_service_names():
        try:
            svc = AIService(svc_name)
        except VersionError as err:
            # If this installadm server doesn't understand this
            # service, it wasn't mounted in the first place
            continue
        if svc.mounted():
            try:
                svc.unmount(force=True)
            except MountError as err:
                print >> sys.stderr, err
                failures += 1
    return failures


class AIService(object):
    '''Class to represent an AI service'''

    MOUNT_ON = com.BOOT_DIR
    X86_BOOTFILE = "boot/grub/pxegrub"
    EARLIEST_VERSION = 2
    LATEST_VERSION = 2

    @classmethod
    def create(cls, name, source_image, ip_start=None, ip_count=None,
               bootserver=None, alias=None, bootargs=''):
        '''Creates a service, setting up configuration, initial manifests,
        and DHCP (if desired)

        Args:
            name: Desired service name (not checked for validity)
            source_image: An InstalladmImage object representing an already
                unpacked image area

        Optional Args:
            ip_start, ip_count: Starting IP address and number of IP addresses
                to allocate in DHCP. If not specified, no DHCP setup will be
                done
            bootserver: If DHCP configuration is provided, optionally explicit
                address for the bootfile server
            alias: An AIService object to which this service will be aliased
            bootargs (x86 only): Additional bootargs to add to menu.lst

        '''
        service = cls(name, _check_version=False)
        service._image = source_image
        service._alias = alias
        service._bootargs = bootargs

        if alias is not None and service.image.version < 3:
            raise UnsupportedAliasError(cw(_("\nCannot create alias: Aliased "
                                             "service '%s' does not support "
                                             "aliasing. Please use a service "
                                             "with a newer image.\n") % alias))

        compatibility_port = (service.image.version < 1)
        logging.debug("compatibility port=%s", compatibility_port)
        server_hostname = socket.gethostname()

        # Determine port information
        if compatibility_port:
            port = get_a_free_tcp_port(server_hostname)
            if not port:
                raise AIServiceError(_("Cannot find a free port to start the "
                                       "web server."))
        else:
            port = libaimdns.getinteger_property(com.SRVINST, com.PORTPROP)

        props = service._init_service_config(server_hostname, port)

        # Create AI_SERVICE_DIR_PATH/<svcdir> structure
        cmd = [com.SETUP_SERVICE_SCRIPT, com.SERVICE_CREATE, name,
               props[config.PROP_TXT_RECORD], service._image.path]
        try:
            # Note: Can't rely on Popen.check_call's logger argument, as
            # the setup-service-script relies on sending information to
            # stdout/stderr
            logging.debug("Executing: %s", cmd)
            Popen.check_call(cmd)
        except CalledProcessError:
            print >> sys.stderr, _("Failed to setup service directory for "
                                   "service, %s\n" % name)

        service._create_default_manifest()

        # Configure DHCP for this service
        service._configure_dhcp(ip_start, ip_count, bootserver)

        # Supported client images will interpolate the bootserver's IP
        # address (as retrieved from the DHCP server) in place of the
        # '$serverIP' string. We choose to utilize this functionality when
        # possible so that any client can utilize this service, from any
        # network.
        if service.image.version < 1:
            # Solaris 11 Express does not support the $serverIP function.
            # We must use the configured AI network's local IP address.
            valid_nets = list(com.get_valid_networks())
            if valid_nets:
                server_ip = valid_nets[0]

                # If more than one IP is configured, inform the user of our
                # choice that we're picking the first one available.
                if len(valid_nets) > 1:
                    print >> sys.stderr, _("More than one IP address is "
                                           "configured for use with "
                                           "install services, using %s for "
                                           "this service.") % server_ip
            else:
                raise AIServiceError(_("Cannot determine bootserver IP."))
        else:
            server_ip = "$serverIP"

        logging.debug("server_ip=%s", server_ip)

        # Save location of service in format <server_ip_address>:<port>
        # It will be used later for setting service discovery fallback
        # mechanism.
        srv_address = '%s:%u' % (server_ip, port)
        logging.debug("srv_address=%s", srv_address)

        # Setup wanboot for SPARC or Grub for x86
        if service.image.arch == 'sparc':
            service._init_wanboot(srv_address)
        else:
            service._init_grub(srv_address)

        return service

    def __init__(self, name, image=None, image_class=InstalladmImage,
                 _check_version=True):
        '''Create a reference to an AIService, which can be used to control
        and manage installadm services.

        Required arguments:
            name: The name of the service. It should already exist.
                     (Use classmethod AIService.create() to make a new service)
        Optional arguments:
            image: A reference to an InstalladmImage object, used to
                      control the underlying image area. If not given,
                      one will be instantiated as needed.
            image_class: If 'image' is omitted, when an image reference is
                            needed, it will be instantiated as an instance
                            of the given class. Defaults to InstalladmImage.

        '''
        self._name = name
        self._image = image
        self._alias = None
        self._bootargs = None
        self._image_class = image_class

        # _check_version should ONLY be used by AIService.create during service
        # creation. Bypassing the version check when the service has already
        # been created may lead to indeterminate results.
        if not _check_version:
            return
        version = self.version()
        if version is None:
            raise VersionError(name, version,
                               alt_msg=_("Unable to determine service "
                                         "version for '%s'" % name))
        elif version < self.EARLIEST_VERSION:
            raise OlderVersionError(name, version)
        elif version > self.LATEST_VERSION:
            raise NewerVersionError(name, version)

    def enable(self):
        '''Enable this service. May fail, if this service is marked in need
        of re-verification, and re-verification fails.

        '''
        if config.get_service_props(self.name).get(config.PROP_REVERIFY,
                                                   False):
            self.check_valid()
        self.mount()
        config.enable_install_service(self.name)

    def disable(self, arch_safe=False, force=False):
        '''Disable this service'''
        props = {config.PROP_STATUS: config.STATUS_OFF}
        config.set_service_props(self.name, props)

        self._unregister()
        self.unmount(arch_safe=arch_safe, force=force)

        # If no longer needed, put install instance into
        # maintenance
        config.check_for_enabled_services()

    def _unregister(self):
        cmd = [com.SETUP_SERVICE_SCRIPT, com.SERVICE_DISABLE, self.name]
        Popen.check_call(cmd, check_result=Popen.ANY)

    def delete(self):
        '''Deletes this service, removing the image area, mountpoints,
        and lingering configuration.

        '''
        self.disable(arch_safe=True, force=True)

        self.remove_profiles()
        for path in self.get_files_to_remove():
            force_delete(path)

    def version(self):
        '''Look up and return the version of this service. See module
        docstring for info on what each service version supports

        '''
        props = config.get_service_props(self.name)
        try:
            return int(props[config.PROP_VERSION])
        except (KeyError, ValueError, TypeError):
            return None

    @property
    def config_dir(self):
        '''Returns the path to the configuration directory for this service'''
        return os.path.join(AI_SERVICE_DIR_PATH, self.name)

    @property
    def database_path(self):
        '''Returns the path to the database file for this service'''
        return os.path.join(self.config_dir, "AI.db")

    def database(self):
        '''Opens an AI_database.DB() object pointing to this service's
        database

        '''
        return AIdb.DB(self.database_path)

    def rename(self, new_name):
        '''Change this service's name to "new_name" updating all relevant
        files, aliases and clients.

        '''
        self._update_name_in_service_props(new_name)
        new_svcdir = os.path.join(AI_SERVICE_DIR_PATH, new_name)
        if self.arch == 'sparc':
            # Update the system.conf file with the new service name
            sysconf_path = os.path.join(self.config_dir, SYSTEMCONF)
            new_str = 'install_service=' + new_name + '\n'
            for line in fileinput.input(sysconf_path, inplace=1):
                if line.startswith('install_service='):
                    sys.stdout.write(new_str)
                else:
                    sys.stdout.write(line)
            self._setup_install_conf()

            # If this is current default sparc service and it is
            # eventually allowed to be renamed, redo symlinks in
            # /etc/netboot:
            #   wanboot.conf -> /etc/netboot/<defaultsvc>/wanboot.conf
            #   system.conf  -> /etc/netboot/<defaultsvc>/system.conf
            # by calling: self.do_default_sparc_symlinks(new_name)
        else:
            # Update the menu.lst file with the new service name
            # Note that the new svcname is assumed to be the mountpoint
            # in /etc/netboot
            menulstpath = os.path.join(self.config_dir, MENULST)
            grub.update_svcname(menulstpath, new_name, new_name)

        self._migrate_service_dir(new_svcdir)
        self._setup_manifest_dir(new_name=new_name)

    def is_default_arch_service(self):
        ''' Determine if this is the default-<arch> service'''

        return (self.name in DEFAULT_ARCH)

    def do_default_sparc_symlinks(self, svcname):
        ''' Do symlinks for default sparc service
        Input:
            svcname - svcname in symlink

        '''
        for linkname in WANBOOTCONF, SYSTEMCONF:
            self._do_default_sparc_symlink(linkname, svcname)

    def _do_default_sparc_symlink(self, linkname, svcname):
        ''' Do symlinks for default sparc service
        Input:
            linkname = either WANBOOTCONF or SYSTEMCONF
            svcname - name of service

        '''
        symlink = os.path.join(self.MOUNT_ON, linkname)
        try:
            os.remove(symlink)
        except OSError as err:
            if err.errno != errno.ENOENT:
                raise
        new_target = os.path.join(self.MOUNT_ON, svcname, linkname)
        os.symlink(new_target, symlink)

    def delete_default_sparc_symlinks(self):
        '''Delete symlinks for default sparc service'''
        for linkname in WANBOOTCONF, SYSTEMCONF:
            symlink = os.path.join(self.MOUNT_ON, linkname)
            try:
                os.remove(symlink)
            except OSError as err:
                if err.errno != errno.ENOENT:
                    raise

    def _migrate_service_dir(self, newsvcdir):
        '''Copy or move the AI_SERVICE_DIR_PATH/<svc> directory
           Additionally update any compatibility symlinks pointing to it
               (or create new ones)

           Input:
                  newsvcdir - full path to new service dir

        '''
        # update any symlinks pointing to svcdir
        dirlist = os.listdir(AI_SERVICE_DIR_PATH)
        for subdir in dirlist:
            fullpath = os.path.join(AI_SERVICE_DIR_PATH, subdir)
            # if not a symlink, skip
            if not os.path.islink(fullpath):
                continue
            # get target of symlink and see if it's the service
            # being renamed
            linkto = os.readlink(fullpath)
            target = os.path.join(os.path.dirname(fullpath), linkto)
            if target == self.config_dir:
                logging.debug("removing %s", fullpath)
                try:
                    os.remove(fullpath)
                except OSError as err:
                    if err.errno != errno.ENOENT:
                        raise
                logging.debug("symlink from %s to %s", fullpath, newsvcdir)
                os.symlink(newsvcdir, fullpath)

        logging.debug("rename from %s to %s", self.config_dir, newsvcdir)
        os.rename(self.config_dir, newsvcdir)

    def _update_name_in_service_props(self, newsvcname):
        '''Set service_name property to newsvcname'''

        props = config.get_service_props(self.name)
        props[config.PROP_SERVICE_NAME] = newsvcname
        config.set_service_props(self.name, props)

    def _update_path_in_service_props(self, new_path):
        '''Set image_path property to new_path '''

        props = config.get_service_props(self.name)
        props[config.PROP_IMAGE_PATH] = new_path
        config.set_service_props(self.name, props)

    @property
    def name(self):
        '''Service name'''
        return self._name

    @staticmethod
    def _prepare_target(mountpoint):
        logging.debug("_prepare_target: mountpoint is %s", mountpoint)
        try:
            os.makedirs(mountpoint)
        except OSError as err:
            if err.errno != errno.EEXIST:
                raise

    @property
    def mountpoint(self):
        '''Returns the path to where this service's image should be mounted'''
        return os.path.join(self.MOUNT_ON, self.name)

    @property
    def bootmountpt(self):
        '''Returns the path to where this service's boot specific config
        file should be mounted

        '''
        if self.arch == 'i386':
            return os.path.join(self.mountpoint, MENULST)
        else:
            return os.path.join(self.mountpoint, SYSTEMCONF)

    def all_bootmountpts(self):
        return [os.path.join(self.mountpoint, MENULST),
                os.path.join(self.mountpoint, SYSTEMCONF)]

    @property
    def bootsource(self):
        '''Returns the path to the file that should be mounted at
        self.bootmountpt

        '''
        if self.arch == 'i386':
            return os.path.join(self.config_dir, MENULST)
        else:
            return os.path.join(self.config_dir, SYSTEMCONF)

    @property
    def dhcp_bootfile(self):
        '''Returns a string that represents what should be added to DHCP
        as this service's bootfile

        '''
        if self.arch == 'sparc':
            http_port = libaimdns.getinteger_property(com.SRVINST,
                                                      com.PORTPROP)
            # Always use $serverIP keyword as setup-dhcp will
            # substitute in the correct IP addresses.
            dhcpbfile = 'http://%s:%u/%s' % ("$serverIP", http_port,
                                             com.WANBOOTCGI)
        else:
            abs_dhcpbfile = os.path.join(self.mountpoint, self.X86_BOOTFILE)
            dhcpbfile = os.path.relpath(abs_dhcpbfile, self.MOUNT_ON)
        return dhcpbfile

    def _lofs_mount(self, from_path, to_mountpoint):
        '''Internal lofs mount function. If the mountpoint
        is already in use by this service, returns without doing anything.
        Otherwise, will mount from_path on to_mountpoint, raising MountError
        for any failure.

        '''
        mount_points = com.MNTTab()['mount_point']
        specials = com.MNTTab()['special']
        if to_mountpoint in mount_points:
            mntpt_idx = mount_points.index(to_mountpoint)
            if specials[mntpt_idx] == from_path:
                # Already mounted as desired; nothing to do
                return
        self._prepare_target(to_mountpoint)
        cmd = [MOUNT, '-F', 'lofs', from_path, to_mountpoint]
        try:
            Popen.check_call(cmd, stdout=Popen.STORE, stderr=Popen.STORE,
                             logger='', stderr_loglevel=logging.DEBUG,
                             check_result=Popen.SUCCESS)
        except CalledProcessError as err:
            raise MountError(from_path, to_mountpoint, err.popen.stderr)

    def mount(self):
        '''Perform service lofs mounts

        For all services,
          /etc/netboot/<svcname> is an lofs mount of <targetdir> (or
               self.image.path for an alias)
        In addition:
          For sparc:
            /etc/netboot/<svcname>/system.conf is an lofs mount of
                AI_SERVICE_DIR_PATH/<svcname>/system.conf
          For x86:
            /etc/netboot/<svcname>/menu.lst is an lofs mount of
                AI_SERVICE_DIR_PATH/<svcname>/menu.lst

        '''
        # Verify the image and ensure the mountpoints exist
        try:
            self.image.verify()
        except ImageError as error:
            # verify doesn't know anything about services so prepend the
            # service name to the exception to give the user sufficient
            # context to fix the problem and re-raise the exception
            raise ImageError(cw(_('Service Name: %s\n') %
                             self.name + str(error)))

        # Mount service's image to /etc/netboot/<svcname>
        self._lofs_mount(self.image.path, self.mountpoint)
        # Do second lofi mount of menu.lst(x86) or system.conf(sparc)
        self._lofs_mount(self.bootsource, self.bootmountpt)

    def unmount(self, force=False, arch_safe=False):
        '''Unmount the service, reversing the effects of AIService.mount()

        Will raise MultipleUnmountFailure if the service does not cleanly
        unmount. If errors occur during unmounting, but the unmounts
        are otherwise successful, then failures are ignored (logged)

        If arch_safe is True, this command will umount all potential
        mountpoints, without checking the underlying image arch
        (useful for deleting a service when the underlying image
        is corrupted or missing)

        DO NOT UNMOUNT AN ENABLED SERVICE OR YOU WILL HAVE WEIRD ERRORS

        SMF will see a service as enabled and remount it at arbitrary
        points in time

        '''
        umount_cmd = [UNMOUNT]
        if force:
            umount_cmd.append("-f")

        if arch_safe:
            mountpoints = self.all_bootmountpts()
        else:
            mountpoints = [self.bootmountpt]

        # The image mountpoint must be unmounted last, as the boot mounts
        # are mounted inside of it.
        mountpoints.append(self.mountpoint)

        failures = list()
        for mountpoint in mountpoints:
            cmd = list(umount_cmd)
            cmd.append(mountpoint)
            try:
                Popen.check_call(cmd, stdout=Popen.STORE, stderr=Popen.STORE,
                                 logger='', stderr_loglevel=logging.DEBUG,
                                 check_result=Popen.SUCCESS)
            except CalledProcessError as err:
                failures.append(UnmountError(mountpoint, err.popen.stderr))

        still_mounted = self._currently_mounted()
        if still_mounted:
            raise MultipleUnmountError(still_mounted, failures)

    def mounted(self):
        '''Returns True if ALL mounts for this service are active.'''
        mount_points = com.MNTTab()['mount_point']
        return (self.mountpoint in mount_points and
                self.bootmountpt in mount_points)

    def _currently_mounted(self):
        '''Returns True if ANY mounts for this service are active.

        If AIService.mounted returns False while AIService.partially_mounted
        returns True, the service failed to properly mount or unmount

        '''
        current_mounts = list()
        mount_points = com.MNTTab()['mount_point']
        if self.mountpoint in mount_points:
            current_mounts.append(self.mountpoint)
        for boot_mount in self.all_bootmountpts():
            if boot_mount in mount_points:
                current_mounts.append(boot_mount)
        return current_mounts

    def is_alias(self):
        '''Determine if this service is an alias'''
        return (self.basesvc is not None)

    def is_aliasof(self, servicename):
        '''
        Determine if this service is an alias of servicename
        '''
        basesvcs = config.get_aliased_services(servicename)
        if self.name in basesvcs:
            return True
        return False

    def update_wanboot_imagepath(self, oldpath, newpath):
        ''' Update the imagepath in service's wanboot.conf

         Input:
            oldpath - imagepath to replace
            newpath - new imagepath

        '''
        logging.debug('update_wanboot_imagepath, %s to %s', oldpath, newpath)
        wanbootpath = os.path.join(self.image.path, WANBOOTCONF)
        for line in fileinput.input(wanbootpath, inplace=1):
            newline = line
            parts = newline.partition(oldpath)
            if parts[1]:
                newline = parts[0] + newpath + parts[2]
            sys.stdout.write(newline)

    def update_basesvc(self, newbasesvc_name):
        '''
        For an alias, do all activities needed if the basesvc
        has been updated:
            All:
            o update aliasof property in .config to newbasesvc_name
            o disable alias
               x86 only:
                  o Copy over new base services's menu.lst to alias' menu.lst
                  o reinstate alias service name in alias' menu.lst
                  o Replace base service bootargs with alias' bootargs in
                    alias' menu.lst
                  o For each dependent alias of alias:
                    o Copy alias' revised menu.lst to dependent alias' menu.lst
                    o reinstate dependent alias name in dependent alias'
                      menu.lst
                    o If bootargs were specified for dependent alias,
                      replace alias bootargs with dependent alias bootargs
                  o For each client of alias:
                    o Copy over alias' revised menu.lst to menu.lst.<clientid>
                    o reinstate alias service_name in menu.lst.<clientid>
                    o If client bootargs were specified during create-client,
                      replace alias bootargs with client bootargs in
                      menu.lst.<clientid>. Otherwise, alias bootargs are
                      inherited.
            o revalidate manifests and profiles
            o enable alias

        Input:   newbasesvc_name - name of new base service

        '''
        if not self.is_alias():
            raise UnsupportedOperationError("This service is not an alias")
        newbasesvc = AIService(newbasesvc_name)
        if newbasesvc.image.version < 3:
            raise UnsupportedAliasError(cw(_("\nCannot update alias: Aliased "
                                             "service '%s' does not support "
                                             "aliasing. Please use a service "
                                             "with a newer image.\n") %
                                             newbasesvc_name))

        # The service configuration needs to be updated before the
        # service can successfully be disabled.
        logging.debug("updating alias service props for %s", self.name)
        props = {config.PROP_ALIAS_OF: newbasesvc_name}
        config.set_service_props(self.name, props)

        # disable the alias
        was_mounted = False
        if self.mounted():
            was_mounted = True
            self.disable(force=True)

        if self.arch == 'i386':
            self_menulstpath = os.path.join(self.config_dir, MENULST)

            all_aliases = config.get_aliased_services(self.name, recurse=True)
            all_clients = config.get_clients(self.name).keys()
            for alias in all_aliases:
                all_clients.extend(config.get_clients(alias).keys())

            # Copy over the new base service's menu.lst file for the alias
            # to use. Replace the base service's name with the alias' name
            # as well as the base service's bootargs with the alias' bootargs
            # in the alias' menu.lst file.
            newbasesvc_menulst = os.path.join(newbasesvc.config_dir, MENULST)
            logging.debug("update_basesvc: copying new menu.lst from %s to %s",
                          newbasesvc_menulst, self_menulstpath)
            shutil.copy(newbasesvc_menulst, self_menulstpath)
            grub.update_svcname(self_menulstpath, self.name, self.name)
            grub.update_bootargs(self_menulstpath, newbasesvc.bootargs,
                                 self.bootargs)

            # Recreate menu.lst files of all aliases of this alias
            # (and descendants). Use the sub alias' bootargs instead
            # of the ones specified in the base service.
            for alias in all_aliases:
                aliassvc = AIService(alias)
                sub_alias_was_mounted = False
                if aliassvc.mounted():
                    sub_alias_was_mounted = True
                    aliassvc.disable(force=True)

                sub_alias_menulst = os.path.join(aliassvc.config_dir, MENULST)
                logging.debug("update_basesvc: copying new menu.lst from %s "
                              "to %s", self_menulstpath, sub_alias_menulst)
                shutil.copy(self_menulstpath, sub_alias_menulst)
                grub.update_svcname(sub_alias_menulst, aliassvc.name,
                                    aliassvc.name)
                grub.update_bootargs(sub_alias_menulst, self.bootargs,
                                     aliassvc.bootargs)
                if sub_alias_was_mounted:
                    aliassvc.enable()

            # recreate menu.lst files for clients
            for clientid in all_clients:
                (service, datadict) = config.find_client(clientid)
                client_bootargs = datadict.get(config.BOOTARGS, '')
                client_menulst = get_client_menulst(clientid)

                # copy alias' menu.lst file to menu.lst.<clientid>
                logging.debug("update_basesvc: copying new menu.lst from %s "
                              "to %s", self_menulstpath, client_menulst)
                shutil.copy(self_menulstpath, client_menulst)
                grub.update_svcname(client_menulst, self.name, self.name)

                # if the client has bootargs, use them. Otherwise, inherit
                # the bootargs specified in the alias (do nothing)
                if client_bootargs:
                    grub.update_bootargs(client_menulst, self.bootargs,
                                         client_bootargs)

        # Turning point: Function calls prior to this line will reference
        # the 'old' base service's image. Function calls after this line
        # will reference the *new* base service's image. Keep that in mind
        # when adding to this function.
        self._image = None

        try:
            self.check_valid()
        except InvalidServiceError:
            print >> sys.stderr, cw(_("\nOne or more of this alias' manifests "
                                      "or profiles is no longer valid. This "
                                      "service will be disabled until they "
                                      "have been removed or fixed.\n"))

        # Enable the alias to pick up the new image
        if was_mounted:
            self.enable()

    @property
    def manifest_dir(self):
        '''Full path to the directory where this service's
        manifests are stored

        '''
        return os.path.join(self.config_dir, "AI_data")

    def validate_manifests(self):
        '''Re-verifies all relevant AI XML manifests associated with this
        service. For any that are no longer valid according to the image's
        DTD, an error is printed. A count of the number of invalid manifests
        is returned.

        '''
        db_conn = self.database()
        manifests = AIdb.getNames(db_conn.getQueue(), AIdb.MANIFESTS_TABLE)

        failures = 0
        for manifest in manifests:
            manifest_path = os.path.join(self.manifest_dir, manifest)
            try:
                DataFiles.from_service(self, manifest_file=manifest_path)
            except ValueError as val_err:
                error = cw(_("\nManifest '%s' is not valid for this image. "
                             "Please fix and update this manifest.\n") %
                             manifest)
                print >> sys.stderr, val_err
                print >> sys.stderr, error
                failures += 1
        return failures

    def remove_profiles(self):
        ''' delete profile files from internal database
        Arg: service - object of service to delete profile files for
        Effects: all internal profile files for service are deleted
        Exceptions: OSError logged only, considered non-critical
        Note: database untouched - assumes that database file will be deleted
        '''
        logging.debug("removing profiles...")
        dbn = self.database()
        queue = dbn.getQueue()
        if not AIdb.tableExists(queue, AIdb.PROFILES_TABLE):
            return
        query = AIdb.DBrequest("SELECT file FROM " + AIdb.PROFILES_TABLE)
        queue.put(query)
        query.waitAns()
        for row in iter(query.getResponse()):
            filename = row['file']
            try:
                if os.path.exists(filename):
                    os.unlink(filename)
            except OSError, emsg:
                logging.warn(_("Could not delete profile %s: %s") %
                             (filename, emsg))

    def validate_profiles(self):
        '''Re-verifies all relevant SC XML profiles associated with this
        service. For any that are no longer valid according to the image's
        DTD, an error is printed. A count of the number of invalid profiles
        is returned.

        '''
        failures = 0

        if self.image.version < 2:
            # Older client images do not support profiles
            return failures
        db_conn = self.database()
        queue = db_conn.getQueue()
        profile_request = AIdb.DBrequest("select name, file from " +
                                         AIdb.PROFILES_TABLE)
        queue.put(profile_request)
        profile_request.waitAns()
        profiles = profile_request.getResponse() or list()

        for profile_name, profile_path in profiles:
            valid = validate_file(profile_name, profile_path,
                                  self.image.path, verbose=False)
            if not valid:
                error = cw(_("\nProfile '%s' is not valid for this image. "
                             "Please fix and update this profile.\n") %
                             profile_name)
                print >> sys.stderr, error
                failures += 1
        return failures

    def check_valid(self):
        '''Validates whether or not this service can be enabled. If
        this service can be enabled, then the PROP_REVERIFY flag is set
        to False. If it CANNOT be enabled, the PROP_REVERIFY flag is set to
        True, the service is disabled, and InvalidServiceError is raised.

        Currently, this function only validates manifests/profiles against
        this service's image (valuable for aliases).

        '''
        invalid_manifest_count = self.validate_manifests()
        invalid_profile_count = self.validate_profiles()
        if invalid_manifest_count or invalid_profile_count:
            # Set invalid flag, and disable service. Service cannot be
            # re-enabled until it comes back as valid.
            config.set_service_props(self.name, {config.PROP_REVERIFY: True})
            self.disable()
            raise InvalidServiceError()
        else:
            # Invalid flag is cleared, but user must manually enable service
            config.set_service_props(self.name, {config.PROP_REVERIFY: False})

    @property
    def basesvc(self):
        '''get reference to basesvc of an alias or None if not an alias'''
        basesvc = None
        props = config.get_service_props(self.name)
        if config.PROP_ALIAS_OF in props:
            basesvc = props[config.PROP_ALIAS_OF]
        return basesvc

    @property
    def bootargs(self):
        '''get bootargs of a service'''
        bootargs = ''
        props = config.get_service_props(self.name)
        if config.PROP_BOOT_ARGS in props:
            bootargs = props[config.PROP_BOOT_ARGS]
        return bootargs

    @property
    def arch(self):
        '''Defer to underlying image object'''
        if self.image:
            return self.image.arch
        else:
            return None

    @property
    def image(self):
        '''Returns a reference to an InstalladmImage object (or subclass)
        pointed at this service's image path (following aliases as needed).

        The actual class of the returned object can be modified by
        passing in the desired class as the "image_class" keyword arg
        to the AIService constructor.
        '''
        if self._image is None:
            # If service is configured, get image path from config and
            # generate an InstalladmImage object. Otherwise, return None
            if config.is_service(self.name):
                props = config.get_service_props(self.name)
                if config.PROP_ALIAS_OF not in props:
                    path = props[config.PROP_IMAGE_PATH]
                else:
                    # if setting up an alias, use the image path of the
                    # service being aliased
                    alias = props[config.PROP_ALIAS_OF]
                    alias_svc = AIService(alias)
                    path = alias_svc.image.path
                self._image = self._image_class(path)
        return self._image

    def _configure_dhcp(self, ip_start, ip_count, bootserver):
        '''Add DHCP configuration elements for this service.'''

        logging.debug('Calling setup_dhcp_server %s %s %s %s %s',
            ip_start, ip_count, bootserver, self.dhcp_bootfile, self.arch)

        setup_dhcp_server(self, ip_start, ip_count, bootserver)

    def _init_grub(self, srv_address):
        '''Initialize grub (menu.lst) for this service'''
        grub.setup_grub(self.name, self.image.path,
                        self.image.read_image_info(), srv_address,
                        self.config_dir, self._bootargs)

    def _init_wanboot(self, srv_address):
        '''Create system.conf file in AI_SERVICE_DIR_PATH/<svcname>'''
        cmd = [com.SETUP_SPARC_SCRIPT, com.SPARC_SERVER, self.image.path,
               self.name, srv_address, self.config_dir]
        # Note: Can't rely on Popen.check_call's logger argument, as
        # the setup-service-script relies on sending information to
        # stdout/stderr
        logging.debug('Calling %s', cmd)
        # SETUP_SPARC_SCRIPT does math processing that needs to run in "C"
        # locale to avoid problems with alternative # radix point
        # representations (e.g. ',' instead of '.' in cs_CZ.*-locales).
        # Because ksh script uses built-in math we need to set locale here and
        # we can't set it in script itself
        modified_env = os.environ.copy()
        lc_all = modified_env.get('LC_ALL', '')
        if lc_all != '':
            modified_env['LC_MONETARY'] = lc_all
            modified_env['LC_MESSAGES'] = lc_all
            modified_env['LC_COLLATE'] = lc_all
            modified_env['LC_TIME'] = lc_all
            modified_env['LC_CTYPE'] = lc_all
            del modified_env['LC_ALL']
        modified_env['LC_NUMERIC'] = 'C'
        Popen.check_call(cmd, env=modified_env)
        self._setup_install_conf()

    def _setup_install_conf(self):
        '''Creates the install.conf file, as needed for compatibility
        with older sparc clients

        '''
        if self.image.version < 3:
            # AI clients starting with this version use
            # system.conf as their source for the "install_service"
            # parameter during boot. The content of system.conf
            # is identical to the install.conf file used by prior
            # image versions; for compatibility with those older
            # clients, we create a symlink.
            #
            # Note that the symlink is created in the image path itself;
            # this implies that legacy sparc services CANNOT be
            # properly aliased - a client booting the alias would get
            # the service name for the base service.
            install_conf = os.path.join(self.image.path, "install.conf")
            system_conf = self.bootsource
            try:
                os.remove(install_conf)
            except OSError as err:
                if err.errno != errno.ENOENT:
                    raise
            os.symlink(system_conf, install_conf)

    def _init_service_config(self, hostname, port):
        '''Initialize this service's .config file. This should only be
        called during service creation, from AIService.create()

        '''
        txt_record = '%s=%s:%u' % (com.AIWEBSERVER, hostname, port)
        logging.debug("txt_record=%s", txt_record)

        service_data = {config.PROP_SERVICE_NAME: self.name,
                        config.PROP_TXT_RECORD: txt_record,
                        config.PROP_GLOBAL_MENU: True,
                        config.PROP_REVERIFY: False,
                        config.PROP_STATUS: config.STATUS_ON}

        if self._alias:
            service_data[config.PROP_ALIAS_OF] = self._alias
        else:
            service_data[config.PROP_IMAGE_PATH] = self.image.path

        if self.arch == 'i386' and self._bootargs:
            service_data[config.PROP_BOOT_ARGS] = self._bootargs
        if self.arch == 'sparc':
            service_data[config.PROP_GLOBAL_MENU] = False

        logging.debug("service_data=%s", service_data)

        config.create_service_props(self.name, service_data)

        return service_data

    def get_files_to_remove(self):
        '''Returns a list of file paths to clean up on service deletion.
        For version 1 services, this includes:
            * The mountpoint in /etc/netboot
            * The configuration directory in AI_SERVICE_DIR_PATH
            * The symlink from AI_SERVICE_DIR_PATH/<port> to the config dir,
              if applicable
            * The symlink from the webserver's docroot to the manifest dir

        '''
        files = [self.mountpoint,
                 self.config_dir,
                 os.path.join(AI_SERVICE_DIR_PATH,
                              config.get_service_port(self.name)),
                 os.path.join(com.WEBSERVER_DOCROOT, self.name)]

        if not self.is_alias():
            files.append(self.image.path)

            # must strip the leading path separator from image_path as
            # os.join won't concatenate two absolute paths
            webserver_path = os.path.join(com.WEBSERVER_DOCROOT,
                                          self.image.path.lstrip(os.sep))
            files.append(webserver_path)

            # find the longest empty path leading up to webserver image path
            if os.path.lexists(webserver_path):
                # get the parent dir of the webserver path
                directory = os.path.dirname(webserver_path)

                # iterate up the directory structure (toward / from the
                # image server's image path) adding empty directories
                while len(os.listdir(directory)) == 1:
                    # Go no further than webserver_base
                    if directory == com.WEBSERVER_DOCROOT:
                        break
                    files.append(directory)
                    directory = os.path.dirname(directory)
        else:
            if self.is_default_arch_service() and self.arch == 'sparc':
                self.delete_default_sparc_symlinks()

        return files

    def _setup_manifest_dir(self, new_name=None):
        '''Create a symlink in the AI webserver's docroot pointing to
        the directory containing this service's manifests

        '''
        if new_name is not None:
            try:
                os.remove(os.path.join(com.WEBSERVER_DOCROOT, self.name))
            except OSError as err:
                if err.errno != errno.ENOENT:
                    raise
            data_dir = os.path.join(AI_SERVICE_DIR_PATH, new_name, "AI_data")
            linkname = os.path.join(com.WEBSERVER_DOCROOT, new_name)
        else:
            data_dir = self.manifest_dir
            linkname = os.path.join(com.WEBSERVER_DOCROOT, self.name)
        if os.path.islink(linkname) or os.path.exists(linkname):
            os.remove(linkname)
        os.symlink(data_dir, linkname)

    def _create_default_manifest(self):
        '''Create an initial, default manifest for the service (preferring
        the manifest contained in the image, but falling back to the one
        on the host system as necessary.

        The manifest directory is setup first, as well.
        (See _setup_manifest_dir)

        '''
        self._setup_manifest_dir()

        default_xml = os.path.join(self.image.path,
                                   IMG_AI_DEFAULT_MANIFEST)
        if not os.path.exists(default_xml):
            # Support older service images by checking for the
            # default manifest at the previous location.
            default_xml = os.path.join(self.image.path,
                                       OLD_IMG_AI_DEFAULT_MANIFEST)
            if not os.path.exists(default_xml):
                print (_("Warning: Using default manifest %s") %
                       SYS_AI_DEFAULT_MANIFEST)
                default_xml = SYS_AI_DEFAULT_MANIFEST

        manifest_name = "orig_default"
        data = DataFiles.from_service(self,
                                      manifest_file=default_xml,
                                      manifest_name=manifest_name,
                                      set_as_default=True)
        insert_SQL(data)
        manifest_path = os.path.join(self.manifest_dir, manifest_name)
        place_manifest(data, manifest_path)
        self.set_default_manifest(manifest_name)

    def set_default_manifest(self, manifest_name, skip_manifest_check=False):
        '''Make manifest "manifest_name" the default for this service'''
        if not skip_manifest_check:
            manifest_path = "/".join([self.manifest_dir, manifest_name])
            if not os.path.isfile(manifest_path):
                raise ValueError(_("\nManifest '%s' does not exist.\n") %
                                 manifest_path)

        config.set_service_props(self.name,
                                 {config.PROP_DEFAULT_MANIFEST: manifest_name})

    def get_default_manifest(self):
        '''Return the name of the default for this service'''
        props = config.get_service_props(self.name)
        return props[config.PROP_DEFAULT_MANIFEST]

    def relocate_imagedir(self, new_path):
        '''Change the location of a service's image:
             o disable the service and all dependent aliases
             o update wanboot.conf for the service (sparc)
             o update menu.lst files of service and its clients and
               aliases (x86)
             o update service .config file
             o move the image and update webserver symlink
             o enable service and dependent aliases
        '''
        was_mounted = False
        if self.mounted():
            was_mounted = True
            self.disable(force=True)

        # get list of all aliases
        all_aliases = config.get_aliased_services(self.name, recurse=True)

        # Determine which aliases are enabled and disable them,
        # saving list of enabled aliases for later.
        enabled_aliases = list()
        for alias in all_aliases:
            aliassvc = AIService(alias)
            if aliassvc.mounted():
                enabled_aliases.append(alias)
                aliassvc.disable(force=True)

        # For x86, update menu.lst files (of service, clients, and aliases)
        # with the new imagepath.
        # For sparc, update wanboot.conf with the new imagepath.
        # There is nothing to do for sparc aliases or clients because they
        # reference the service's wanboot.conf file.
        if self.arch == 'i386':
            self_menulstpath = os.path.join(self.config_dir, MENULST)
            grub.update_imagepath(self_menulstpath, self.image.path, new_path)

            all_clients = config.get_clients(self.name).keys()
            for alias in all_aliases:
                all_clients.extend(config.get_clients(alias).keys())

            for clientid in all_clients:
                client_menulstpath = get_client_menulst(clientid)
                grub.update_imagepath(client_menulstpath, self.image.path,
                                      new_path)
            for alias in all_aliases:
                aliassvc = AIService(alias)
                alias_menulstpath = os.path.join(aliassvc.config_dir, MENULST)
                grub.update_imagepath(alias_menulstpath, aliassvc.image.path,
                                      new_path)
        elif self.arch == 'sparc':
            self.update_wanboot_imagepath(self.image.path, new_path)

        # update image_path in .config file
        self._update_path_in_service_props(new_path)

        # relocate image and update webserver symlinks
        self.image.move(new_path)

        # enable service and aliases
        if was_mounted:
            self.enable()
        for alias in enabled_aliases:
            AIService(alias).enable()


def get_a_free_tcp_port(hostname):
    ''' get next free tcp port

        Looks for next free tcp port number, starting from 46501

        Input:   hostname
        Returns: next free tcp port number if one found or
                 None if no free port found

    '''
    # determine ports in use by other install services
    existing_ports = set()
    allprops = config.get_all_service_names()
    for svcname in allprops:
        port = config.get_service_port(svcname)
        existing_ports.add(int(port))

    logging.debug("get_a_free_tcp_port, existing_ports=%s", existing_ports)

    mysock = None
    for port in xrange(STARTING_PORT, ENDING_PORT):
        try:
            # skip ports of existing install services
            if port in existing_ports:
                continue
            mysock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            mysock.bind((hostname, port))
        except socket.error:
            continue
        else:
            logging.debug("found free tcp port: %s" % port)
            mysock.close()
            break
    else:
        logging.debug("no available tcp port found")
        return None

    return port


def get_client_menulst(clientid):
    '''get path to client menu.lst.<clientid> file'''
    return (os.path.join(NETBOOT, MENULST + '.' + clientid))


_DHCP_MSG = """No local DHCP configuration found. This service is the default
alias for all %s clients. If not already in place, the following should
be added to the DHCP configuration:"""


def setup_dhcp_server(service, ip_start, ip_count, bootserver):
    '''Set-up DHCP server for given AI service and IP address range'''

    _incomplete_msg = cw(_("\nThe install service has been created but the "
                           "DHCP configuration has not been completed. Please "
                           "see dhcpd(8) for further information.\n"))

    bootfile = service.dhcp_bootfile
    arch = service.arch

    logging.debug("setup_dhcp_server: ip_start=%s, ip_count=%s, "
                  "bootfile=%s, arch=%s", ip_start, ip_count, bootfile, arch)

    server = dhcp.DHCPServer()
    svc_cmd = False
    if ip_count:
        # The end-user has requested that DHCP be configured on this server
        # by passing the IP start and IP count arguments. We will add to the
        # configuration and then take the proper action for the SMF service.
        if server.is_online():
            # DHCP server is configured and running. We will add to the
            # configuration and then restart the SMF service.
            svc_cmd = 'restart'
        else:
            if not server.is_configured():
                # We need to configure and enable a new DHCP server. Lay the
                # base configuration down and then enable the SMF service.
                # Note we will not enable an existing DHCP configuration if the
                # SMF service is offline.
                print cw(_("\nStarting DHCP server..."))
                server.init_config()
                svc_cmd = 'enable'

        try:
            print cw(_("Adding IP range to local DHCP configuration"))
            server.add_address_range(ip_start, ip_count, bootserver)
        except dhcp.DHCPServerError as err:
            print >> sys.stderr, cw(_("\nUnable to add IP range: %s\n" % err))
            print >> sys.stderr, _incomplete_msg
            return

    # Regardless of whether the IP arguments are passed or not, now check the
    # current DHCP configuration for default service configuration. If we're
    # creating a new default alias for this architecture, set this service's
    # bootfile as the default in the DHCP local server.
    if arch == 'sparc':
        bfile = dhcp.fixup_sparc_bootfile(bootfile)
        client_type = 'SPARC'
    else:
        bfile = bootfile
        client_type = 'PXE'

    if server.is_configured():
        if service.is_default_arch_service():
            try:
                if server.arch_class_is_set(arch):
                    # There is a class for this architecture already in place
                    cur_bootfile = server.get_bootfile_for_arch(arch)
                    if cur_bootfile is None:
                        # No bootfile set for this arch
                        print cw(_("Setting the default %s bootfile in the "
                                   "local DHCP configuration to '%s'\n") %
                                   (client_type, bfile))
                        server.add_bootfile_to_arch(arch, bootfile)
                    elif cur_bootfile != bfile:
                        # Update the existing bootfile to our default
                        print cw(_("Updating the default %s bootfile in the "
                                   "local DHCP configuration from '%s' to "
                                   "'%s'\n") %
                                   (client_type, cur_bootfile, bfile))
                        server.update_bootfile_for_arch(arch, bootfile)
                else:
                    # Set up a whole new architecture class
                    print cw(_("Setting the default %s bootfile in the local "
                               "DHCP configuration to '%s'\n") %
                               (client_type, bfile))
                    server.add_arch_class(arch, bootfile)

            except dhcp.DHCPServerError as err:
                print >> sys.stderr, cw(_("Unable to update the local DHCP "
                                          "configuration: %s\n" % err))
                print >> sys.stderr, _incomplete_msg
                return

            # Make sure we kick the DHCP server, if needed
            if server.is_online and not svc_cmd:
                svc_cmd = 'restart'

        # Configuration is complete, update the SMF service accordingly
        if svc_cmd:
            try:
                server.control(svc_cmd)
            except dhcp.DHCPServerError as err:
                print >> sys.stderr, cw(_("\nUnable to update the DHCP SMF "
                                          "service after reconfiguration: "
                                          "%s\n" % err))
                print >> sys.stderr, cw(_("\nThe install service has been "
                                          "created and the DHCP configuration "
                                          "has been updated, however the DHCP "
                                          "SMF service requires attention. "
                                          "Please see dhcpd(8) for further "
                                          "information.\n"))
    elif service.is_default_arch_service():
        # DHCP configuration is not local and this is the default service for
        # this this architecture. Provide DHCP boot configuration information.
        valid_nets = list(com.get_valid_networks())
        if valid_nets:
            server_ip = valid_nets[0]

        print _(_DHCP_MSG % client_type)

        if arch == 'i386':
            # Boot server IP is not needed for SPARC clients
            print _("\t%-20s : %s" % ("Boot server IP", server_ip))

        print _("\t%-20s : %s\n" % ("Boot file", bfile))

        if len(valid_nets) > 1 and arch == 'i386':
            print cw(_("\nNote: determined more than one IP address "
                       "configured for use with AI. Please ensure the above "
                       "'Boot server IP' is correct.\n"))


if __name__ == '__main__':
    if sys.argv[1] == "mount-all":
        sys.exit(mount_enabled_services())
    elif sys.argv[1] == "remount":
        sys.exit(mount_enabled_services(remount=True))
    elif sys.argv[1] == "unmount-all":
        sys.exit(unmount_all())
    else:
        sys.exit("Invalid arguments")
