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
# Copyright (c) 2010, Oracle and/or its affiliates. All rights reserved.
#
'''Defines the DataObjectDict class to allow storage of a dictionary in cache.
'''

from lxml import etree

from solaris_install.data_object import \
    DataObjectBase, ParsingError


class DataObjectDict(DataObjectBase):
    ''' Storage Object to store a dictionary in the
    Data Object Cache.

    By default it will not generate any XML, if it is required that the data
    would generate XML, then you need to pass generate_xml=True to the
    constructor.

    XML Generated is along the lines of:

        <data_dictionary>
            <data name="key">value</data>
            ...
        </data_dictionary>

    It is possible to change the tag and sub-tag names by sub-classing this
    object along the lines of:

        class DataObjectDictDifferentTags(DataObjectDict):
            # Override both TAG_NAME and SUB_TAG_NAME
            TAG_NAME = "different_tag"
            SUB_TAG_NAME = "different_sub_tag"
            pass

    it is necessary to do things this way to ensure that the class methods
    can_handle() and from_xml() can behave as expected.

    Sub-classes DataObjectNoChildManipulation since we want this to be
    a leaf-node object.

    '''

    TAG_NAME = "data_dictionary"
    SUB_TAG_NAME = "data"

    def __init__(self, name, data_dict, generate_xml=False):
        '''Initialize the object with the provided data_dict.

        This method takes the following parameters:

        name         - the name of the object

        data_dict   - a python dictionary object containing the data.

        generate_xml - boolean to say whether this will generate XML or not.
                       (default: False)

        Exceptions:

        ValueError   - Will be raised if any invalid values are passed as
                       parameters.

        '''

        super(DataObjectDict, self).__init__(name)

        if not isinstance(data_dict, dict):
            raise ValueError("data_dict parameter is not a python 'dict'.")

        self._data_dict = data_dict

        self._generate_xml = generate_xml

    # Override abstract functions fron DataObject class.
    def to_xml(self):
        '''Generate XML to represent a dictionary.

        Generates XML in the format:

            <data_dictionary>
                <data name="NAME">VALUE</data>
                ...
            </data_dictionary>

        The tags and sub-tags are defined by the class attributes TAG_NAME
        and SUB_TAG_NAME - to change, you should sub-class this class and
        set their values.
        '''
        if not self.generate_xml:
            return None

        element = etree.Element(self.TAG_NAME, name=self.name)
        for k in sorted(self.data_dict.keys()):
            sub_element = etree.SubElement(element, self.SUB_TAG_NAME)
            sub_element.set("name", str(k))
            sub_element.text = str(self.data_dict[k])

        return element

    @classmethod
    def can_handle(cls, xml_node):
        '''Determines if this class can import XML as generated by to_xml().

        The class attributes TAG_NAME and SUB_TAG_NAME are used to determine
        if this is possible.
        '''
        if xml_node.tag == cls.TAG_NAME:
            for child in xml_node:
                if child.tag != cls.SUB_TAG_NAME:
                    # Fail if we find anything that isn't the sub-tag.
                    return False
            return True
        else:
            return False

    @classmethod
    def from_xml(cls, xml_node):
        '''Imports XML as generated by to_xml().

        The class attributes TAG_NAME and SUB_TAG_NAME are used when
        doing the conversion.
        '''
        new_obj = None

        if xml_node.tag == cls.TAG_NAME:
            new_dict = dict()
            new_obj = DataObjectDict(xml_node.get("name"), new_dict,
                generate_xml=True)

            # Populate child nodes into dictionary.
            for child in xml_node:
                if child.tag != cls.SUB_TAG_NAME:
                    # Fail if we find anything that isn't the sub-tag.
                    raise ParsingError("Invalid tag in data_dict: %s" \
                        % (child.tag))
                new_dict[child.get("name")] = child.text
        else:
            raise ParsingError("Invalid tag in data_dict: %s" %
                (xml_node.tag))

        return new_obj

    # Attributes accessors and mutators
    def data_dict():
        '''Access to the contained data_dictionary.'''
        def fget(self):
            '''Return the data dictionary being used by object.
            '''
            return self._data_dict

        def fset(self, new_data_dict):
            '''Sets the dictionary to be new_data_dict.

            Exceptions:

            ValueError  - Will be thrown if the value for new_data_dict is
                          not of the correct type.
            '''

            if not isinstance(new_data_dict, dict):
                raise ValueError(
                    "new_data_dict parameter is not a python 'dict'.")

            self._data_dict = new_data_dict

        doc = '''Get/Set the data dictionary

        Exceptions:

        ValueError  - Will be thrown if a new value for data_dict is
                      not of the correct type.
        '''
        return locals()

    data_dict = property(**data_dict())

    def generate_xml():
        def fget(self):
            '''Returns whether this object will generate XML.
            '''
            return self._generate_xml

        def fset(self, generate_xml):
            '''Sets the generate_xml flag'''

            self._generate_xml = generate_xml
        doc = '''True if this object will generate XML'''
        return locals()

    generate_xml = property(**generate_xml())

    def __getattr__(self, attr):
        """Provide access to dictionary values as attributes if desired.

        The primary use of this is when using paths in string substitutions
        and the desire is to refer to a value in the dictionary.
        """
        if self.data_dict is not None:
            try:
                return self.data_dict[attr]
            except KeyError:
                raise AttributeError("Invalid attribute: %s" % (attr))
