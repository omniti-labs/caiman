#!/bin/ksh
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
# Copyright (c) 2008, 2011, Oracle and/or its affiliates. All rights reserved.
#
# Description:
#       It contains common functions used by installadm subcommands
#
# Files potentially changed on server:
# /etc/inetd.conf - to turn on tftp daemon
# /etc/netboot/menu.lst - menu.lst file corresponding to the service/client

AIMDNS=/usr/lib/installadm/aimdns.py
AWK=/bin/awk
BASENAME=/bin/basename
CAT=/bin/cat
CHMOD=/bin/chmod
CP=/bin/cp
CPIO=/bin/cpio
CUT=/bin/cut
DIRNAME=/bin/dirname
EGREP=/bin/egrep
FIND=/bin/find
GREP=/bin/grep
HEAD=/bin/head
HOSTNAME=/bin/hostname
IFCONFIG=/usr/sbin/ifconfig
LN=/bin/ln
MKDIR=/bin/mkdir
MOUNT=/usr/sbin/mount
MV=/bin/mv
NAWK=/bin/nawk
PGREP=/bin/pgrep
PS=/bin/ps
PYTHON=/usr/bin/python2.7
RM=/bin/rm
SED=/bin/sed
SORT=/bin/sort
SVCADM=/usr/sbin/svcadm
SVC_CFG_MODULE=/usr/lib/python2.7/vendor-packages/osol_install/auto_install/service_config.py
SVCCFG=/usr/sbin/svccfg
SVCPROP=/bin/svcprop
SVCS=/bin/svcs
TAIL=/bin/tail
WC=/bin/wc

DEFAULT_GRUB_TITLE="OmniOS"
VERSION="Illumos"
HTTP_PORT=5555

X86_IMAGE="x86_image"
DOT_RELEASE=".release"
DOT_IMAGE_INFO=".image_info"
GRUB_TITLE_KEYWORD="GRUB_TITLE"
GRUB_MIN_MEM64="GRUB_MIN_MEM64"
GRUB_DO_SAFE_DEFAULT="GRUB_DO_SAFE_DEFAULT"
NO_INSTALL_GRUB_MENU="NO_INSTALL_GRUB_TITLE"
NETBOOTDIR="/etc/netboot"
WANBOOT_CONF_FILE="wanboot.conf"
WANBOOT_CONF_SPEC="${NETBOOTDIR}/${WANBOOT_CONF_FILE}"
SMF_SERVICE="svc:/system/install/server"
SMF_INST_SERVER="${SMF_SERVICE}:default"
AIWEBSERVER="aiwebserver"
SERVICE_ADDRESS_UNKNOWN="unknown"
# egrep string to remove any addresses with escaped :'s (IPv6),
# question-marks ? or 0.0.0.0 (both in progress of getting a DHCP
# lease), or -> (IP tunnels)
IPADM_GREP_STRING='\\:|\?|^0.0.0.0|[0-9]->[0-9]'

#
# get_http_port()
#
# Purpose: Get the install SMF service property value for port
#
# Args: None
#
# Returns: 0 - On success
#          1 - On failure
#

function get_http_port
{
	HTTP_PORT=$($SVCPROP -cp all_services/port \
				$SMF_INST_SERVER 2>/dev/null)
	ret=$?
	if [[ "X$HTTP_PORT" == "X" ]] ; then
		HTTP_PORT=5555
	fi

	return $ret
}

#
# address_in_range()
#
# Purpose: Check if an IP address is in a subnet
#
# Args:	   $1 - Network address and subnet in CIDR notation
#		(e.g. 192.168.1.1/24)
#	   $2 - IP address to check
#
# Returns: 0 - On IP in subnet
#	   1 - On IP not in subnet

function address_in_range
{
	# calculate the first address in the network to test in
	typeset net1=$(calculate_net_addr $1)
	# calculate the first address in the IP to test in using the
	# netmask from the network provided
	typeset net2=$(calculate_net_addr ${2}/${1#*/})
	if [[ "$net1" == "$net2" ]]; then
		return 0
	fi
	return 1
}

#
# get_ip_for_net()
#
# Purpose: Function gets the network interface IP address (e.g. 192.168.1.60) from a network
# 	   address (e.g. 192.168.1.0).
#
# Args:	   $1 - Network address
#
# Returns: 0 - On success
#	   1 - On failure
#	   Prints to standard out the machine's IP address for that network

function get_ip_for_net
{
	if (( $# != 1 )); then
		print -u2 'Need one argument, for get_ip_for_net(): a' \
		    'network address'
		exit 1
	fi

	typeset network=$1
	typeset server_ip=""

	# First, see if IP address is only reachable via the default route
	# in which case we will just use the nodename's IP address
	typeset destination=$(/usr/sbin/route -n get -inet -host $network | \
	    $GREP '^[^a-zA-Z0-9]*destination: ' | \
	    $SED 's/^[^a-zA-Z0-9]*destination: //')
	if [[ "$destination" == "default" ]]; then
		server_ip=$(/usr/bin/getent hosts $(uname -n))

		if [[ -z "$server_ip" ]]; then
			print -u2 "Warning: unable to find IP address for" \
			    "nodename."
			return 1
		fi

		# strip off the hostname portion of getent(1)'s response
		print "${server_ip%%[^0-9.]*}"
		return 0
	fi

	# Get preferred interface for the network address looking at
	# route(1)'s interface field in case of more than one
	# interface on a network segment or IPMP
	typeset interface=$(/usr/sbin/route -n get -inet -host $network | \
	    $GREP '^[^a-zA-Z0-9]*interface: ' | \
	    $SED 's/^[^a-zA-Z0-9]*interface: //')

	if [[ -z "$interface" ]]; then
		print -u2 "Warning: unable to find outgoing network" \
			  "interface for network $network."
		return 1
	fi

	# Find the IP address(es) for the interface route(1) provided. Use
	# ipadm(1) to look up interface addresses;
	server_ip=$(/usr/sbin/ipadm show-addr -p -o ADDR ${interface}/ | \
		    $EGREP -v -- "$IPADM_GREP_STRING")

	# see if interface has multiple VLANs
	if (( $(print "$server_ip" | $WC -l) > 1 )); then
		# find an interface which serves the given IP (as route won't
		# tell us the specific interface instance) -- we pick the first
		# matching interface address returned by ipadm(1) show-addr
		for if_addr in $server_ip; do
			# test if the network we are looking for is in the
			# network served by the current interface address
			if address_in_range $if_addr $network; then
				# store the interface address
				server_ip=$if_addr
				# stop searching, now that we found a match
				break
			fi
		done
	fi

	if [[ -z "$server_ip" ]]; then
		print -u2 "Warning: unable to find IP address"\
			  "for network interface $interface."
		return 1
	fi
	# strip off trailing netmask - string should be of the form:
	# 192.168.1.1/24
	server_ip=${server_ip/%\/[0-9]*/}
	print "$server_ip"
	return 0
}

#
# get_ip_netmask
#
# Purpose : Get the netmask set for the given IP address on the current host.
#           Assumes IP address is currently set on an interface.
#
# Arguments :
#	$1 - IP address
#
# Returns netmask in hexidecimal notation (e.g. ffffff00)
#
get_ip_netmask()
{
	ipaddr=$1

	if [[ -z "$ipaddr" ]]; then
		return
	fi

	$IFCONFIG -a | $GREP broadcast | $AWK '{print $2, $4}' | \
		while read t_ipaddr t_netmask ; do
			if [ "$t_ipaddr" = "$ipaddr" ]; then
				print "$t_netmask"
				break
			fi
		done
}

#
# get_grub_title
#
# Purpose: Get the line used in the title line of the grub menu.
#	   If the <image>/.image_info file contains the GRUB_TITLE line
#	   specifying the grub title to be used, the string will be returned.
#	   Otherwise, use the first line of the <image>/.release file as the 
#	   title of the grub menu. If the <image>/.release file does not
#	   exist, return the value of $DEFAULT_GRUB_TITLE. 
#
# Arguments: 
#	$1 - path to image
#
# Returns: String specified with the GRUB_TITLE keyward in <image>/.image_info
#	file.  If no GRUB_TITLE is specified or if <image>/.image_info
#	does not exist, the first line of the <image>/.release file will
#	be returned.  If <image>/.release file is not found, the value of
#	$DEFAULT_GRUB_TITLE will be returned.
#
get_grub_title()
{

	grub_title=""

	image_info_path=$1/${DOT_IMAGE_INFO}
	if [[ -f ${image_info_path} ]]; then
		while read line ; do
			if [[ "${line}" == ~(E)^${GRUB_TITLE_KEYWORD}=.* ]]
			then
				grub_title="${line#*=}" 
			fi
		done < ${image_info_path}
	fi

	if [[ -z "${grub_title}" ]]; then
		releasepath=$1/${DOT_RELEASE}
		if [[ -f ${releasepath} ]]; then
			grub_title=$($HEAD -1 ${releasepath})
		else
			grub_title=$DEFAULT_GRUB_TITLE
		fi
	fi
	print "$grub_title"
}

#
# get_grub_min_mem64
#
# Purpose: Get minimum memory required to boot network AI in 64 bit mode.
#	   GRUB menu.lst is then populated with 'min_mem64' option accordingly.
#
# Arguments: 
#	$1 - path to image
#
# Returns: String specified with the GRUB_MIN_MEM64 keyword
#	in <image>/.image_info file. If no GRUB_MIN_MEM64 is specified or if
#	<image>/.image_info does not exist, return an empty string.
#
get_grub_min_mem64()
{

	grub_min_mem64=""

	image_info_path=$1/${DOT_IMAGE_INFO}
	if [[ -f ${image_info_path} ]]; then
		while read line ; do
			if [[ "${line}" == ~(E)^${GRUB_MIN_MEM64}=.* ]]
			then
				grub_min_mem64="${line#*=}"
			fi
		done < ${image_info_path}
	fi

	print "$grub_min_mem64"
}

#
# get_grub_do_safe_default
#
# Purpose: Get flag from the image indicating whether or not the grub menu
#	   should be created with a safe default entry (i.e. a default entry
#	   that does not start an automated install.)
#
# Arguments:
#	$1 - path to image
#
# Returns:
#	true - if GRUB_DO_SAFE_DEFAULT is set to true in the
#	       <image>/.image_info file.
#	false - otherwise
#
get_grub_do_safe_default()
{
	grub_do_safe_default=""

	image_info_path=$1/${DOT_IMAGE_INFO}
	if [[ -f ${image_info_path} ]]; then
                while read line ; do
                        if [[ "${line}" == ~(E)^${GRUB_DO_SAFE_DEFAULT}=.* ]]
                        then
                                grub_do_safe_default="${line#*=}"
                        fi
                done < ${image_info_path}
        fi

	if [[ -z "$grub_do_safe_default" || \
	    "$grub_do_safe_default" != "true" ]] ; then
		grub_do_safe_default="false"
	fi

	print "$grub_do_safe_default"
}

#
# get_grub_text_mode_menu
#
# Purpose: Get the title to be used for the default menu option
#
# Arguments:
#      $1 - path to image 
#
# Returns: string with menu title 
#
get_grub_text_mode_menu()
{
	
	grub_text_mode_menu=""

	image_info_path=$1/${DOT_IMAGE_INFO}
	if [[ -f ${image_info_path} ]]; then
                while read line ; do
                        if [[ "${line}" == ~(E)^${NO_INSTALL_GRUB_MENU}=.* ]]
                        then
                                grub_text_mode_menu="${line#*=}"
                        fi
                done < ${image_info_path}
        fi

	# Default to the value in .release or VERSION to remain consistent
	# If there is not a title specified in .image_info we assume that 
	# the image doesn't have the text install capability
	if [[ -z "${grub_text_mode_menu}" ]]; then
		releasepath=$1/${DOT_RELEASE}
		if [[ -f ${releasepath} ]]; then
			grub_text_mode_menu=$(head -1 ${releasepath})
		else
			grub_text_mode_menu=$VERSION
		fi
		grub_text_mode_menu="$grub_text_mode_menu boot image"
	fi

	print "$grub_text_mode_menu"
}
#
# get_service_address
#
# Purpose: Get the service location (machine ip and port number) for a given
#          service name. The AI service's install properties are consulted
#          for this information.
#
# Arguments: 
#	$1 - service name
#
# Returns: service address in format <machine_ip>:<port_number> or
#          if server is multi-homed "$serverIP:<port_number>";
#          if service not found in SMF, "unknown" is returned
#
#
get_service_address()
{
	#
	# Search for the txt_record in the AI service's SMF properties.
	# The data is stored as a property of the AI service's property group.
	#
	# ...
	# txt_record: aiwebserver=<machine_hostname>:<machine_port>
	# ...
	#
	svcname=$1
	srv_location=$($PYTHON $SVC_CFG_MODULE listprop ${svcname} txt_record)
	srv_location="${srv_location#*=}"

	# if location of service can't be obtained, return with "unknown"
	if [[ -z "$srv_location" ]] ; then
		print "$SERVICE_ADDRESS_UNKNOWN"
		return 0
	fi

	# get the hostname portion of the txt_record property
	# which is of the form <hostname>:<port>
	srv_address_hostname="${srv_location%:*}"

	# see if the machine has more than one interface up/or no interfaces up
	if (( $(valid_networks | $WC -l) != 1  )); then
		# for multi-homed AI servers use the "$serverIP" keyword
		srv_address_ip='$serverIP'
	else
		srv_address_ip=$(get_ip_for_net $(valid_networks))
	fi

	# get the port portion of the txt_record property
	# which is of the form <hostname>:<port>
	srv_address_port="${srv_location#*:}"

	# if port or IP can't be determined, return with "unknown"
	if [[ -n "$srv_address_ip" ]] && [[ -n "$srv_address_port" ]] ; then
		print "${srv_address_ip}:${srv_address_port}"
	else
		print "$SERVICE_ADDRESS_UNKNOWN"
	fi

	return 0
}

#
# valid_networks
#
# Purpose: Get the networks on which AI is to be used cross checked against
# networks available on the server and networks permitted or denied in SMF
#
# Arguments: optional full_ip - indicates whether or not the ip address
#				should be the full address or the supernet
#				address.  For example, 10.0.2.15/24
#				would become 10.0.2.15 if full_ip is true.
#				Otherwise, it would be 10.0.2.0.
#				By default the function produces the supernet
#				address.  Passed to other functions.
#
# Returns: Networks to be used, white space delimited, printed on standard
# out with each network's network address and no subnet information
# NOTE: This function caches the networks to be found so subsequent runs,
#       though quicker will not reflect updates to the system. Run
#       'unset VALID_NETWORKS' to re-acquire the data, if needed.
#
function valid_networks
{
	typeset final_nets

	full_ip=false
	if (( $# == 1 )); then
		full_ip=$1
	fi

	# get interfaces which the server provides
	nets=""

	for net in $(get_system_networks $full_ip); do
		# strip the network bits
		nets="${nets}${net%/*}\n"
	done

	# get the SMF networks to be included or excluded
	smf_nets=$(get_SMF_masked_networks)

	# apply the SMF mask to the system's networks (record the network
	# output and ignore the bad masks output)
	reduced_nets=$(apply_mask_to_networks $(print "${nets}\n${smf_nets}") \
	    3>&1 4>/dev/null)

	# strip padding off IP addresses
	for ip in $reduced_nets; do
		ip=$(strip_ip_address $ip)
		final_nets="${final_nets}${ip} "
	done

	# strip off the trailing space in final_nets
	final_nets="${final_nets% }"

	# if no networks are available print nothing
	if [[ -z "${final_nets// /\\n}" ]]; then
		return
	fi
	print "${final_nets// /\\n}"
}

#
# get_system_networks
# (to be a support function for valid_networks)
#
# Purpose: Get the networks available on the server
#
# Arguments: optional full_ip - indicates whether or not the ip address
#				should be the full address or the supernet
#				address.  For example, 10.0.2.15/24
#				would become 10.0.2.15 if full_ip is true.
#				Otherwise, it would be 10.0.2.0.
#				By default the function produces the supernet
#				address.  Passed to other functions.
#
# Returns: Networks available, white space delimited, printed on standard
# out with subnet bits in CIDR notation and duplicates filtered out
#
function get_system_networks
{
	full_ip=false
	if (( $# == 1 )) ; then
		full_ip=$1
	fi

	# get all addresses and
	# remove <IPv6 | 127.0.0.1 | unconfigured DHCP interfaces>
	interfaces=$(/usr/sbin/ipadm show-addr -p -o ADDR,STATE | \
	    $EGREP -v "${IPADM_GREP_STRING}|^127.0.0.1" | \
	    $EGREP -e ':ok$|:tentative$' | \
	    $SED 's/:ok$//;s/:tentative$//')
	networks=""
	for interface in $interfaces; do
		# save the network bits
		bits=${interface#*/}
		net=$(calculate_net_addr $interface $full_ip)
		networks="${networks}${net}/${bits}\n"
	done

	# only print unique networks (we might have multiple interfaces
	# per network which would otherwise result in duplicates)
	networks=$(print $networks | \
	    $SORT -u -t . -k 1,1n -k 2,2n -k 3,3n -k 4,4n)

	print -n "$networks"
}

#
# get_SMF_masked_networks
# (to be a support function for valid_networks)
#
# Purpose: Get the networks which SMF either has listed for explicit inclusion
# or for exclusion from AI server setup
#
# Arguments:_None
#
# Returns: Networks specified in SMF, white space delimited, printed on standard
# out (and if provided, subnet bits in CIDR notation). A leading plus or minus 
# is used on each line to indicate inclusion or exclusion (but by the AI SMF 
# service design only minus or plus can be used not a mixture)
#
function get_SMF_masked_networks
{
	# get the SMF networks listed for inclusion or exclusion,
	# one per line, sorted
	smf_nets=$($SVCPROP -cp all_services/networks $SMF_INST_SERVER)
	# replace spaces with newlines and sort the output
	smf_nets=$(print ${smf_nets// /\\n} | \
	    $SORT -t . -k 1,1n -k 2,2n -k 3,3n -k 4,4n)

	# exclude will be true or false depending on if we are excluding
	# the networks configured in all_services/networks
	exclude=$($SVCPROP -cp all_services/exclude_networks $SMF_INST_SERVER)

	prefix="+"
	if [[ "$exclude" == "true" ]]; then
		prefix="-"
	fi

	# print networks with leading + or - as appropriate
	for net in $smf_nets; do
		print -- "${prefix}${net}"
	done
	return
}

#
# apply_mask_to_networks
# (to be a support function for valid_networks)
#
# Purpose: To apply a mask (as generated by a function like
# get_SMF_masked_networks) to a list of networks (as generated by a function
# like get_system_networks w/o network bits)
#
# Arguments:_A list of networks (as generated by a function like
#	     get_system_networks) and a network mask to apply
#
# Returns: File descriptor 3 - Networks which meet the mask criteria,
#			       newline delimited
# 	   File descriptor 4 - Any mask entries which do not apply newline
#			       delimited
#
# Return code: 0 - all networks which were provided as a mask matched
#	       1 - not all networks provided in mask matched
#
function apply_mask_to_networks
{
	typeset mask_nets="" # store mask network
	typeset mask_ips="" # store mask bare IP addresses
	typeset networks="" # store networks to mask against
	typeset match_nets="" # store networks matched by mask
	typeset status=0 # store our return value
	# parse arguments populating $mask_nets, $mask_ips and $networks
	for entry in $*; do
		# if entry starts with a + or - then it is a mask entry
		if [[ "$entry" == ~(E)^[+-] ]]; then
			# store whether we are including or excluding networks
			# (+/- respectively)
			prefix=${entry:0:1}
			# if entry ends in /[0-9]+ then it should
			# have a CIDR-notation netmask -- and be a network
			if [[ "$entry" == ~(E)/[0-9]+ ]]; then
				mask_nets="${mask_nets}$entry "
			# this should be a bare IP address
			else
				mask_ips="${mask_ips}$entry "
			fi
		else
			# networks need to be one per line for use with egrep(1) below
			networks="${networks}$entry "
		fi
	done

	# strip off trailing space
	networks="${networks% }"
	# convert spaces to \n's in $networks and then to newlines
	networks="${networks// /\\n}"
	networks=$(print "${networks#\\n/}")

	# As the SMF net_address_v4 data type used for networks can support
	# either a bare IPv4 address or IPv4/CIDR netmask, if handed only an IP
	# determine if the IP address is valid for any network available on the 
	# system
	for ip in $mask_ips; do
		# record if this mask address has yet been matched to a network
		typeset matched=false
		# skip the first character +/- (depending on exclude value)
		ip=${ip:1}
		# iterate over each system subnet and determine if the SMF
		# provided IP address is included in that subnet when a match
		# is found, add that network to $mask_nets
		for sys_net in $(get_system_networks); do
			if address_in_range $sys_net $ip; then
				# we got a match - add this network to the list
				# of networks to mask (use the same prefix too)
				mask_nets="${mask_nets}${prefix}${sys_net} "
				# no need to keep looking at this IP
				matched=true
				break
			fi
		done
		if ! ${matched}; then
			# we failed to match a network with this IP address
			# report that to file descriptor 4
			print -u4 "$ip"
			status=1
		fi
	done

	# we don't support partial network matches so ensure the
	# system's network falls within the bounds of one of the SMF
	# network specified

	typeset matched_masks # store masks matched to ensure they all matched
	for sys_net in $networks; do
		# run over each SMF defined network and cross check
		# them with all system networks
		for mask_net in $mask_nets; do
			# skip the +/- in each network network to mask
			if address_in_range ${mask_net:1} $sys_net; then
				# we got a match!

				# match_nets is space delimited to make putting
				# in newlines or other delimiters easy with
				# sed(1)
				match_nets="${match_nets}${sys_net} "
				# store that this mask matched
				matched_masks+="${mask_net} "
				# no need to keep looking at SMF networks
				break
			fi
		done
	done

	# see if we matched all masks
	if (( ${#mask_nets} != ${#matched_masks} )); then
		# run through each mask seeing that we matched it or else warn
		for mask in $mask_nets; do
			if [[ -z "$matched_masks" || \
			    "${matched_masks}" == ~(E)"${mask} " ]]; then
				# we failed to match a network with this IP address
				# report that to file descriptor 4 (and strip off
				# the +/- prefix)
				print -u4 "${mask:1}"
				status=1
			fi
		done
	fi

	# strip off the trailing space in match_nets
	match_nets="${match_nets% }"

	# if we are including only the listed networks (mask_nets starts with a
	# positive) then return match_nets
	if [[ "$prefix" == "+" ]]; then
		# replace spaces with \n's which print will make newlines
		match_nets="${match_nets// /\\n}"
		final_networks="$match_nets"
	else
	# we are excluding the found networks
		if [[ -n $match_nets ]]; then
			# grep out networks which were matched
			# (combine the networks to look for with |'s)
			match_nets="${match_nets/ /|}"
			# the egrep will fail to catch the last address
			final_networks=$(print "$networks" | \
			    $EGREP -v "${match_nets% }")
		fi
	fi

	print -u3 "$final_networks"
	return $status
}

#
# pad_ip_address
# (to be a support function for valid_networks)
#
# Purpose: To pad leading 0's on individual IP address octets
#
# Arguments:_An IP address (such as '192.168.1.0')
#
# Returns: Padded IP address on standard out
#
function pad_ip_address
{
	ip=$1
	typeset final_ip

	# add padding zeros to ip addresses
	# run through every octet of the IP address adding necessary zeros
	OIFS="$IFS"
	IFS="."
	for octet in $ip; do
		# ensure octet is three numbers long, if not, make it so
		if (( ${#octet} < 3 )); then
			while (( ${#octet} < 3 )); do
				 octet=0${octet}
			done
		fi
		final_ip="${final_ip}${octet}."
	done
	IFS="$OIFS"
	# strip off trailing dot
	print "${final_ip%.}"
	return
}


#
# strip_ip_address
# (to be a support function for valid_networks)
#
# Purpose: To strip leading 0's off individual IP address octets
#
# Arguments:_An IP address (such as '192.168.001.000')
#
# Returns: Padding striped IP address on standard out
#
function strip_ip_address
{
	ip=$1
	# strip padding zeros off ip addresses
	# run every octet of the IP address through a pattern matching
	# leading zeros then at least one number 0-9 followed by a '.'
	print "${ip/*(0)+([0-9]).*(0)+([0-9]).*(0)+([0-9]).*(0)+([0-9])/\2.\4.\6.\8}"
	return
}

#
# Calculate network start address separated by a comma
# Expects a.b.c.d/network_bits, optional full_ip flag
#
# Arguments: address_n_bits - address and network bits
# 	     optional full_ip - indicates whether or not the ip address
#				should be the full address or the supernet
#				address.  For example, 10.0.2.15/24
#				would become 10.0.2.15 if full_ip is true.
#				Otherwise, it would be 10.0.2.0.
#				By default the function produces the supernet
#				address.  Evaluated in function.
#
# Returns: returns supernet IP address --OR--
#                  full IP address if full_ip is true
#
function calculate_net_addr
{
	address_n_bits=$1
	bits=${address_n_bits#*/}
	a=${address_n_bits%/*}

	if [[ -z $a || -z $bits ]]; then
		print -u2 "Unable to determine address and network bits" \
		    "from $address_n_bits"
		exit 1
	fi

	full_ip=false
	# check for the optional argument
	if (( $# == 2 )); then
		# respond appropriately
		full_ip=$2
		if [ $full_ip == true ]; then
			# output the current IP address and return
			print $a
			return
		fi
	fi

	# load address into an array splitting on dots
	OIFS="$IFS"
	IFS="."
	typeset -a addr=($a)
	IFS="$OIFS"

	numerator=$((2**32))
	denominator=$((2**bits))

	# calculate the number of hosts on the network
	hosts=$((numerator/denominator))

	# calculate the netmask
	typeset -a netmask
	netmask[3]=$((fmax(0,2**8-hosts)))
	# using exponent notation seems to break the next three lines per bug
	# 16349 - ksh93 arithmetic oddities
	# they are 2**8, 2**16 and 2**24 respectively
	netmask[2]=$((fmax(0,2**8-fmax(1,hosts/256))))
	netmask[1]=$((fmax(0,2**8-fmax(1,hosts/65536))))
	netmask[0]=$((fmax(0,2**8-fmax(1,hosts/16777216))))

	# zero pad to three places
	typeset -Z3 tmp
	# calculate padded start address
	typeset -a s_addr
	for i in 0 1 2 3; do
		tmp=$((addr[i]&netmask[i]))
		s_addr[i]=$tmp
	done

	print "${s_addr[0]}.${s_addr[1]}.${s_addr[2]}.${s_addr[3]}"
	return
}


#
# find_network_attr
#
# Purpose : Given an IP address, figure out which network on this
#	    server it belongs to, or its netmask, depending on $2.
#	    Workhorse function for find_network(), find_network_nmask() and
#	    find_network_baseIP()
#
# Parameters :
#	$1 - IP address
#	$2 - what gets returned: one of "network", "netmask" or "netIPaddr"
#		- "network" specifies that this function returns the network
#			corresponding to the IP address (IP addr & netmask)
#		- "netmask" specifies that this function returns the netmask
#			of the network corresponding to the IP address
#		- "netIPaddr" specifies that this function returns the base IP
#			address of the network corresponding to the IP address
# Returns :
#	Network for IP address passed in.
#
find_network_attr()
{
	typeset ipaddr=$1
	typeset attr=$2

	if [[ -z "$ipaddr" ]] ; then
		return
	fi

	# Iterate through the interfaces to figure what the possible
	# networks are (in case this is a multi-homed server).
	# For each network, use its netmask with the given IP address 
	# to see if resulting network matches.
	$IFCONFIG -a | $GREP broadcast | $AWK '{print $2, $4}' | \
		while read t_ipaddr t_netmask ; do

			# convert hex netmask into bits for CIDR notation
			typeset bits
			# 32 bits minus however many are masked out for hosts
			((bits=32-log2(2**32-16#$t_netmask)))

			# get network of this interface
			if_network=$(calculate_net_addr ${t_ipaddr}/$bits)
			if [[ -z $if_network ]]; then
				continue
			fi

			# get network for passed in ipaddr based
			# on this interfaces's netmask
			ip_network=$(calculate_net_addr ${ipaddr}/$bits)
			if [[ -z $ip_network ]]; then
				continue
			fi

			# if networks match, this is the network that
			# the passed in ipaddr belongs to.
			if [ "$if_network" = "$ip_network" ] ; then
				case $attr in
					"network" )
						print "$if_network"
						;;
					"netmask" )
						print "$t_netmask"
						;;
					"netIPaddr" )
						print "$t_ipaddr"
						;;
				esac
				break
			fi
		done
}

#
# find_network
#
# Purpose : Given an IP address, figure out which network on this
#	    server it belongs to.
#
# Parameters :
#	$1 - IP address
#
# Returns :
#	Network for IP address passed in.
#
find_network()
{
	print $(find_network_attr $1 "network")
}

#
# find_network_nmask()
#
# Purpose : Given an IP address, figure out which network on this server it
#	belongs to, and return that network's netmask.
#
# Parameters :
#	$1 - IP address
#
# Returns :
#	Netmask for IP address passed in.
#
find_network_nmask()
{
	print $(find_network_attr $1 "netmask")
}

#
# find_network_baseIP()
#
# Purpose : Given an IP address, figure out which network on this server it
#	belongs to, and return that network's base IP address.
#
# Parameters :
#	$1 - IP address
#
# Returns :
#	Netmask for IP address passed in.
#
find_network_baseIP()
{
	print $(find_network_attr $1 "netIPaddr")
}
