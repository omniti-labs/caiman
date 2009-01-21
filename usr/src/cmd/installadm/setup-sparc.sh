#!/bin/sh
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
# Copyright 2009 Sun Microsystems, Inc.  All rights reserved.
# Use is subject to license terms.

# Description:
#	This script sets up the wanboot.conf file which is used
#	to boot the sparc netimage.
#
# Files potentially changed on server:
# /etc/netboot - directory created
# /etc/netboot/<network number> - directory created
# /etc/netboot/wanboot.conf - file created
# /etc/netboot/<network number>/<MACID>/wanboot.conf - file created 
# <image>/install.conf - file created 

. /usr/lib/installadm/installadm-common


NETBOOTDIR="/etc/netboot"
WANBOOTCGI="/usr/lib/inet/wanboot/wanboot-cgi"
CGIBINDIR="/var/ai/image-server/cgi-bin"


#
# Create the install.conf file (replace if it already exists)
#
# Arguments:
#	$1 - Name of service
#	$2 - Absolute path to image (where file will be placed)
#
create_installconf()
{
	svcname=$1
	image_path=$2

	installconf=${image_path}/install.conf
	tmpconf=${installconf}.$$

	printf "install_service=" > ${tmpconf}
	printf "${svcname}\n" >> ${tmpconf}

	# Rename the tmp file to the real thing
	mv ${tmpconf} ${installconf}

	return 0
}

#
# Create the wanboot.conf file (replace if it already exists)
#
# Arguments:
#	$1 - directory in which to place the wanboot.conf file
#	$2 - ip address of server
#	$3 - Absolute path to image
#
create_wanbootconf()
{
	confdir=$1
	svr_ip=$2
	image_path=$3

	wanbootconf=${confdir}/wanboot.conf
	tmpconf=${wanbootconf}.$$
	pgrp="sun4v"	# hardcoded for now

	printf "root_server=" > ${tmpconf}
	printf "http://${svr_ip}:${HTTP_PORT}/" >> ${tmpconf}
	printf "${CGIBIN_WANBOOTCGI}\n" >> ${tmpconf}

	printf "root_file=" >> ${tmpconf}
	printf "${image_path}/boot/boot_archive\n" >> ${tmpconf}

	printf "boot_file=" >> ${tmpconf}
	printf "${image_path}/platform/${pgrp}/wanboot\n" >> ${tmpconf}

	printf "encryption_type=\n" >> ${tmpconf}
	printf "signature_type=\n" >> ${tmpconf}
	printf "server_authentication=no\n" >> ${tmpconf}
	printf "client_authentication=no\n" >> ${tmpconf}

	# rename the tmp file to the real thing
	echo "Creating SPARC configuration file"
	mv ${tmpconf} ${wanbootconf}

	return 0
}

		
#
# This is an internal function
# So we expect only limited use

if [ $# -lt 3 ]; then
	echo "Internal function to manage SPARC setup doesn't have enough data"
	exit 1
fi

# get server ip address
srv_ip=`get_server_ip`

# determine network
n1=`echo $srv_ip | cut -d'.' -f1-3`
net=$n1.0

if [ "$1" = "server" ]; then
	img_path=$2
	svc_name=$3

	if [ ! -f "${WANBOOTCGI}" ]; then
		echo "${WANBOOTCGI} does not exist"
		exit 1
	fi

	if [ ! -d "${CGIBINDIR}" ]; then
		echo "${CGIBINDIR} does not exist"
		exit 1
	fi

	# copy over wanboot-cgi
	cp ${WANBOOTCGI} ${CGIBINDIR}

	# create install.conf file at top of image.
	# it contains the service name
	#
	create_installconf $svc_name $img_path

	# create /etc/netboot directories
	#
	mkdir -p ${NETBOOTDIR}/${net}

	create_wanbootconf ${NETBOOTDIR} $srv_ip $img_path
	status=$?

elif [ "$1" = "client" ]; then
	macid=$2
	img_path=$3

	# create /etc/netboot sub-directories
	#
	wbootdir="${NETBOOTDIR}/${net}/${macid}"
	mkdir -p ${wbootdir}

	create_wanbootconf $wbootdir $srv_ip $img_path
	status=$?
else 
	echo " $1 - unsupported SPARC setup service action"
	exit 1
fi


if [ $status -eq 0 ]; then
	exit 0
else
	exit 1
fi