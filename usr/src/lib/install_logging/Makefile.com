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
# Copyright (c) 2010, Oracle and/or its affiliates. All rights reserved.
#
SUBDIRS		=

all		:= TARGET= all
install		:= TARGET= install
clean		:= TARGET= clean
clobber		:= TARGET= clobber
lint		:= TARGET= lint

LIBRARY		= liblogger.a
VERS		= .1

OBJECTS		= logger.o

SRCS =          $(OBJECTS:.o=.c)

include ../../Makefile.lib

SRCDIR		= ..
INCLUDE		= -I/usr/include/python2.7

CPPFLAGS        += ${INCLUDE} -D${ARCH}
CFLAGS		+= $(DEBUG_CFLAGS) -Xa ${CPPFLAGS} -DNDEBUG

all:		$(HDRS) dynamic .WAIT $(SUBDIRS)

static:		$(LIBS)

dynamic:	$(DYNLIB) $(DYNLIBLINK)

lint:		lint_SRCS

$(SUBDIRS):	FRC
	cd $@; pwd; $(MAKE) $(TARGET)

FRC:

include ../../Makefile.targ

