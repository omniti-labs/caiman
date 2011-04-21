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
# Copyright (c) 2011, Oracle and/or its affiliates. All rights reserved.

'''
Contains routines and definitions for any script involving profiles
'''
import os
import sys
from string import Template

import osol_install.auto_install.AI_database as AIdb
import osol_install.auto_install.verifyXML as verifyXML
from osol_install.auto_install.installadm_common import _

INTERNAL_PROFILE_DIRECTORY = '/var/ai/profile' # profiles stored here internally
AI_MANIFEST_ATTACHMENT_NAME = 'manifest.xml' # MIME attachment name for manifest
WEBSERVD_UID = 80 # user ID of webserver daemon
WEBSERVD_GID = 80 # group ID of webserver daemon
TEMPLATE_VARIABLES = [
            'AI_ARCH',
            'AI_CID',
            'AI_CPU',
            'AI_HOSTNAME',
            'AI_IPV4',
            'AI_MAC',
            'AI_MEM',
            'AI_NETWORK',
            'AI_PLATFORM',
            'AI_SERVICE'
            ]

class AICriteriaTemplate(Template):
    ''' Derived class for Python Template class, which provides template string
    substitiution based on a dictionary.  The format is:
        {{ <token> }}
    '''
    delimiter = '{{'
    pattern = r'''
    \{\{(?:
    (?P<escaped>\{\{)|
    (?P<named>[_a-z][_a-z0-9]*)\}\}|
    (?P<braced>[_a-z][_a-z0-9]*)\}\}|
    (?P<invalid>)
    )
    '''


def perform_templating(profile_str, validate_only=True):
    ''' Given profile string, do all template substitutions for any criteria
    found in the process environment
    Args:
        profile_str - profile in a string
        validate_only - boolean, set to True if doing validation only -
            for cases where the client criteria is not available
            If validate_only, do not generate exceptions for criteria missing
            from environment
    Returns:
        profile string with any templating substitution performed
    Exceptions:
        KeyError when template variable missing when it is
            absolutely needed; e.g., install time, installadm for static profile
        ValueError when environment variable format is invalid
    '''
    template_dict = dict()
    # look for all supported criteria in process environment
    for replacement_tag in TEMPLATE_VARIABLES:
        if replacement_tag in os.environ:
            val = os.environ[replacement_tag]
            if replacement_tag == 'AI_MAC':
                try:
                    val = verifyXML.checkMAC(val).upper()
                except ValueError:
                    print >> sys.stderr, _(
                            "Warning: MAC address in enviroment is invalid "
                            "and will be ignored.")
                    continue
            elif replacement_tag == 'AI_NETWORK' or \
                    replacement_tag == 'AI_IPV4':
                try:
                    val = verifyXML.checkIPv4(val)
                except ValueError:
                    print >> sys.stderr, _(
                            "Warning: IP or network address in enviroment is "
                            "invalid and will be ignored.")
                    continue
            template_dict[replacement_tag] = val
            # if the MAC address is defined,
            # and the client ID not already defined in the environment,
            # then derive client ID from the MAC address
            if replacement_tag == 'AI_MAC' and ('AI_CID' not in os.environ or
                    os.environ['AI_CID'] is None):
                client_mac_parts = val.split(":")
                template_dict['AI_CID'] = "01%s%s%s%s%s%s" % \
                    (client_mac_parts[0].zfill(2).lower(),
                     client_mac_parts[1].zfill(2).lower(),
                     client_mac_parts[2].zfill(2).lower(),
                     client_mac_parts[3].zfill(2).lower(),
                     client_mac_parts[4].zfill(2).lower(),
                     client_mac_parts[5].zfill(2).lower())
    # instantiate our template object derived from string Template class
    tmpl = AICriteriaTemplate(profile_str)
    if validate_only:
        # do not insist on all criteria being present in environment
        profile_out = tmpl.safe_substitute(template_dict)
    else:
        # if template variable not in dict - exception KeyError
        profile_out = tmpl.substitute(template_dict)
    return profile_out # profile string with substitutions


def sql_values_from_criteria(criteria, queue, table, gbl=False):
    ''' Given a criteria dictionary, for the indicated DB table
    and queue, return a tuple composed of lists whose elements can be used
    to construct SQLite clauses.  If gbl is true, build a clause that 
    will affect all database records if criteria is missing - a global effect.
    Args:
        criteria - criteria dictionary
        queue - database queue
        table - database table
        gbl - if True, global
    Returns: a tuple for SQLite clauses respectively: WHERE, INTO, VALUES
    '''
    where = list() # for WHERE clause
    intol = list() # for INTO clause
    vals = list() # for VALUES clause
    for crit in AIdb.getCriteria(queue, table, onlyUsed=False, strip=True):
        # Get the value from the manifest
        values = criteria[crit]
        # the critera manifest didn't specify this criteria
        if values is None:
            # if the criteria we're processing is a range criteria, fill in
            # NULL for two columns, MINcrit and MAXcrit
            vals += ["NULL"]
            if AIdb.isRangeCriteria(queue, crit, table):
                where += ["MIN" + crit + " IS NULL"]
                where += ["MAX" + crit + " IS NULL"]
                intol += ["MIN" + crit]
                intol += ["MAX" + crit]
                vals += ["NULL"]
            # this is a single value
            else:
                where += [crit + " IS NULL"]
                intol += [crit]
        # this is a single criteria (not a range)
        elif isinstance(values, basestring):
            # use lower case for text strings
            intol += [crit]
            val = AIdb.format_value(crit, values).lower()
            where += [crit + "=" + val]
            vals += [val]
        # Else the values are a list this is a range criteria
        else:
            # Set the MIN column for this range criteria
            if values[0] == 'unbounded':
                if not gbl:
                    where += ["MIN" + crit + " IS NULL"]
                    intol += ["MIN" + crit]
                    vals += ['NULL']
            else:
                intol += ["MIN" + crit]
                if crit == 'mac':
                    val = AIdb.format_value(crit,
                            verifyXML.checkMAC(values[0])).upper()
                    where += ["HEX(MIN" + crit + ")<=HEX(" + val + ")"]
                else:
                    val = AIdb.format_value(crit, values[0]).lower()
                    where += ["MIN" + crit + "<=" + val]
                vals += [val]
            # Set the MAX column for this range criteria
            if values[1] == 'unbounded':
                if not gbl:
                    where += ["MAX" + crit + " IS NULL"]
                    intol += ["MAX" + crit]
                    vals += ['NULL']
            else:
                intol += ["MAX" + crit]
                if crit == 'mac':
                    val = AIdb.format_value(crit,
                            verifyXML.checkMAC(values[1])).upper()
                    where += ["HEX(MAX" + crit + ")>=HEX(" + val + ")"]
                else:
                    val = AIdb.format_value(crit, values[1]).lower()
                    where += ["MAX" + crit + ">=" + val]
                vals += [val]
    return where, intol, vals


def is_name_in_table(name, queue, table):
    ''' Determine if profile already registered for service and same basename
    Args:
        name - profile name
        queue - database queue for profiles
        table - profile table name
    Returns True if any records are found, False if no records found
    '''
    query_str = "SELECT * FROM %s WHERE name='%s'" % \
        (table, AIdb.sanitizeSQL(name))
    query = AIdb.DBrequest(query_str)
    queue.put(query)
    query.waitAns()
    return len(query.getResponse()) > 0


def get_columns(queue, table):
    ''' From database queue and table, get database columns
    Returns database columns as list
    Args:
        queue - database queue object
        table - database table name
    '''
    query = AIdb.DBrequest("PRAGMA table_info(" + table + ")")
    queue.put(query)
    query.waitAns()
    columns = list()
    # build a query so we can determine which columns (criteria) are in use
    # using the output from the PRAGMA statement
    for col in iter(query.getResponse()):
        columns += [col['name']]
    return columns


def validate_profile_string(profile_str, image_dir, resolve_entities=True,
        dtd_validation=False, warn_if_dtd_missing=False):
    ''' Given the profile contained in a string variable, validate
    Args:
        profile_str - profile in string format
	image_dir - path of service image, used to locate service_bundle
        resolve_entities - if True, ask XML parser to resolve all entities
        dtd_validation - if True, validate against a DTD in the profile
        warn_if_dtd_missing - if True, raise an exception if the DTD not found
    Returns: profile as string with any inclusions
    Exceptions: etree.XMLSyntaxError
    '''
    import lxml.etree as etree
    from StringIO import StringIO

    # create an XMLParser object with settings
    parser = etree.XMLParser(
        # always read DTD for XInclude namespace xi in service_bundle(4)
        load_dtd=True,
        resolve_entities=resolve_entities,
        dtd_validation=False
        )
    root = etree.parse(StringIO(profile_str), parser)
    if not resolve_entities: # just check basic XML, no inclusions
        return profile_str
    # validate against DTD if provided
    if dtd_validation and \
            (root.docinfo.externalDTD is not None or
                    root.docinfo.internalDTD is not None):
        # check for service_bundle(4)
        if root.docinfo.system_url is not None and \
                root.docinfo.system_url.find('/service_bundle.dtd.') == -1:
            print >> sys.stderr, _(
                "Warning:  DOCTYPE %s specified instead of service_bundle(4). "
                "The file might not be a profile.") % root.docinfo.system_url
    dtd_file = os.path.join(image_dir, 'auto_install', 'service_bundle.dtd.1')
    # if warning only on DTD missing, and DTD is indeed missing
    if root.docinfo.system_url is not None and warn_if_dtd_missing and \
	not os.path.exists(dtd_file):
        print >> sys.stderr, _(
            "Warning:  DTD %s not found.  Cannot validate completely.") % \
            dtd_file
        return etree.tostring(root)
    # parse, validating against external DTD
    with open(dtd_file) as dtdfp:
        dtd_string = dtdfp.read()
    dtd = etree.DTD(StringIO(dtd_string))
    root = etree.XML(etree.tostring(root))
    if dtd.validate(root):
    	return etree.tostring(root)
    raise etree.XMLSyntaxError(_('Failed validation against DTD. '
                                 'See service_bundle(4).'), '', '', '')


def validate_criteria_from_user(criteria, dbo, table):
    ''' Validate profile criteria from dictionary containing command line input
    Args:    criteria - Criteria object holding the criteria that is to be
                        added/set for a manifest.
             dbo - AI_database object for the install service.
             table - name of database table
    Raises:  SystemExit if:
        - criteria is not found in database
        - value is not valid for type (integer and hexadecimal checks)
        - range is improper
    Returns: nothing
    '''
    # find all possible profile criteria expressed as DB table columns
    critlist = AIdb.getCriteria(dbo.getQueue(), table, onlyUsed=False,
                                strip=False)
    # verify each range criteria is well formed
    for crit in criteria:
        # gather this criteria's values
        man_criterion = criteria[crit]
        # check "value" criteria here (check the criteria exists in DB
        if isinstance(man_criterion, basestring):
            # only check criteria in use in the DB
            if crit not in critlist:
                raise SystemExit(_(
                    "Error:\tCriteria %s is not a valid criteria!") % crit)
        # This is a range criteria.  (Check that ranges are valid, that
        # "unbounded" gets set to 0/+inf, ensure the criteria exists
        # in the DB
        else:
            # check for a properly ordered range (with unbounded being 0 or
            # Inf.)
            if man_criterion[0] != "unbounded" and \
                man_criterion[1] != "unbounded" and \
                man_criterion[0] > man_criterion[1]: # Check min > max
                raise SystemExit(_(
                    "Error:\tCriteria %s is not a valid range (MIN > MAX) ")
                    % crit)
            # Clean-up NULL's and changed "unbounded"s to 0 and
            # the maximum integer value
            # Note "unbounded"s are already converted to lower case during
            # input processing
            if man_criterion[0] == "unbounded":
                man_criterion[0] = "0"
            if man_criterion[1] == "unbounded":
                man_criterion[1] = str(sys.maxint)
            if crit == "mac":
                # convert hex mac address (w/o colons) to a number
                try:
                    man_criterion[0] = long(str(man_criterion[0]).upper(), 16)
                    man_criterion[1] = long(str(man_criterion[1]).upper(), 16)
                except ValueError:
                    raise SystemExit(_(
                        "Error:\tCriteria %s is not a valid hexadecimal value")
                                % crit)
            else: # this is a decimal value
                try:
                    man_criterion = [long(str(man_criterion[0]).upper()),
                                     long(str(man_criterion[1]).upper())]
                except ValueError:
                    raise SystemExit(_(
                        "Error:\tCriteria %s is not a valid integer value") %
                            crit)
            # check to see that this criteria exists in the database columns
            if 'MIN' + crit not in critlist and 'MAX' + crit not in critlist:
                raise SystemExit(_(
                    "Error:\tCriteria %s is not a valid criteria!") % crit)