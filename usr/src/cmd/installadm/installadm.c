/*
 * CDDL HEADER START
 *
 * The contents of this file are subject to the terms of the
 * Common Development and Distribution License (the "License").
 * You may not use this file except in compliance with the License.
 *
 * You can obtain a copy of the license at usr/src/OPENSOLARIS.LICENSE
 * or http://www.opensolaris.org/os/licensing.
 * See the License for the specific language governing permissions
 * and limitations under the License.
 *
 * When distributing Covered Code, include this CDDL HEADER in each
 * file and include the License file at usr/src/OPENSOLARIS.LICENSE.
 * If applicable, add the following below this CDDL HEADER, with the
 * fields enclosed by brackets "[]" replaced with your own identifying
 * information: Portions Copyright [yyyy] [name of copyright owner]
 *
 * CDDL HEADER END
 */

/*
 * Copyright 2009 Sun Microsystems, Inc.  All rights reserved.
 * Use is subject to license terms.
 */

#include <stdio.h>
#include <locale.h>
#include <sys/param.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <unistd.h>
#include <string.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <errno.h>

#include "installadm.h"

typedef int cmdfunc_t(int, char **, const char *);

static cmdfunc_t do_create_service, do_delete_service;
static cmdfunc_t do_list, do_start, do_stop;
static cmdfunc_t do_create_client, do_delete_client;
static cmdfunc_t do_add, do_remove, do_set;
static cmdfunc_t do_version, do_help;
static void do_opterr(int, int, const char *);
static uint16_t get_a_free_tcp_port(uint16_t);
static void save_service_data(char *, char *, char *, char *);
static void remove_service_data(char *, char *, char *, char *);
static boolean_t get_service_data(char *, char *, char *, char *);
static int installadm_system(char *);

static char *progname;

typedef struct cmd {
	char		*c_name;
	cmdfunc_t	*c_fn;
	const char	*c_usage;
} cmd_t;

static cmd_t	cmds[] = {
	{ "create-service",		do_create_service,
	    "\tcreate-service\t[-d] [-u] [-f <bootfile>] [-D <DHCPserver>] \n"
	    "\t\t\t[-n <svcname>] [-i <dhcp_ip_start>] \n"
	    "\t\t\t[-c <count_of_ipaddr>] [-s <srcimage>] <targetdir>"	},

	{ "delete-service",	do_delete_service,
	    "\tdelete-service\t[-x] <svcname>"				},

	{ "list",	do_list,
	    "\tlist\t[-n <svcname>]"					},

	{ "start",	do_start,
	    "\tstart\t<svcname>"					},

	{ "stop",	do_stop,
	    "\tstop\t<svcname>"						},

	{ "create-client",	do_create_client,
	    "\tcreate-client\t[-P <protocol>] \n"
	    "\t\t\t[-b \"<property>=<value>\"] \n"
	    "\t\t\t-e <macaddr> -t <imagepath> -n <svcname>"		},

	{ "delete-client",	do_delete_client,
	    "\tdelete-client\t<macaddr>"				},

	{ "add",	do_add,
	    "\tadd\t-m <manifest> -n <svcname>"				},

	{ "remove",	do_remove,
	    "\tremove\t-m <manifest> -n <svcname>"			},

	{ "set",	do_set,
	    "\tset\t-p <name>=<value> -n <svcname>"			},

	{ "version",	do_version,
	    "\tversion"							},

	{ "help",	do_help,
	    "\thelp\t[<subcommand>]"					}

};

static void
usage(void)
{
	int	i;
	cmd_t	*cmdp;

	(void) fprintf(stderr, MSG_INSTALLADM_USAGE);
	for (i = 0; i < sizeof (cmds) / sizeof (cmds[0]); i++) {
		cmdp = &cmds[i];
		if (cmdp->c_usage != NULL)
			(void) fprintf(stderr, "%s\n", gettext(cmdp->c_usage));
	}
	exit(INSTALLADM_FAILURE);
}


int
main(int argc, char *argv[])
{
	int	i;
	cmd_t	*cmdp;

	(void) setlocale(LC_ALL, "");

	/*
	 * Must have at least one additional argument to installadm
	 */
	if (argc < 2) {
		usage();
	}

	progname = argv[0];

	/*
	 * If it is valid subcommand, call the do_subcommand function
	 * found in cmds. Pass it the subcommand's argc and argv, as
	 * well as the subcommand specific usage.
	 */
	for (i = 0; i < sizeof (cmds) / sizeof (cmds[0]); i++) {
		cmdp = &cmds[i];
		if (strcmp(argv[1], cmdp->c_name) == 0) {
			if (cmdp->c_fn(argc - 1, &argv[1], cmdp->c_usage)) {
				exit(INSTALLADM_FAILURE);
			} else {
				exit(INSTALLADM_SUCCESS);
			}
		}
	}

	/*
	 * Otherwise, give error and print usage
	 */
	(void) fprintf(stderr, MSG_UNKNOWN_SUBCOMMAND,
	    progname, argv[1]);
	usage();

	exit(INSTALLADM_FAILURE);
}


static int
call_script(char *scriptname, int argc, char *argv[])
{
	int	i;
	char	cmd[BUFSIZ];
	char	cmdargs[BUFSIZ];


	cmdargs[0] = '\0';
	for (i = 0; i < argc; i++) {
		(void) strcat(cmdargs, argv[i]);
		(void) strcat(cmdargs, " ");
	}

	(void) snprintf(cmd, sizeof (cmd), "%s %s",
	    scriptname, cmdargs);

	return (installadm_system(cmd));

}

/*
 * do_create_service:
 * This function parses the command line arguments and sets up
 * the image, the DNS service, the network configuration for the
 * the clients to boot from this image (/tftpboot) and dhcp if desired.
 * This function calls shell scripts to handle each of the tasks
 */
static int
do_create_service(int argc, char *argv[], const char *use)
{
	int		opt;
	boolean_t	make_service_default = B_FALSE;
	boolean_t	publish_as_unicast = B_FALSE;
	boolean_t	named_service = B_FALSE;
	boolean_t	named_boot_file = B_FALSE;
	boolean_t	dhcp_setup_needed = B_FALSE;
	boolean_t	create_netimage = B_FALSE;
	boolean_t	use_remote_dhcp_server = B_FALSE;
	boolean_t	create_service = B_FALSE;
	boolean_t	have_sparc = B_FALSE;

	char		*boot_file = NULL;
	char		*ip_start = NULL;
	short		ip_count;
	char		*service_name = NULL;
	char		*source_path = NULL;
	char		*dhcp_server = NULL;
	char		*target_directory = NULL;

	struct stat	stat_buf;
	char		cmd[MAXPATHLEN];
	char		mpath[MAXPATHLEN];
	char		bfile[MAXPATHLEN];
	char		srv_name[MAXPATHLEN];
	char		txt_record[DATALEN];
	char		dhcp_macro[MAXNAMELEN+12]; /* dhcp_macro_<filename> */
	int		size;

	while ((opt = getopt(argc, argv, "du:b:n:i:c:s:D")) != -1) {
		switch (opt) {
		/*
		 * Make this service as default
		 * It is not yet supported
		 */
		case 'd':
			make_service_default = B_TRUE;
			break;
		/*
		 * Publish this service as unicast DNS
		 * It is not yet supported
		 */
		case 'u':
			publish_as_unicast = B_TRUE;
			break;
		/*
		 * Create a boot file for this service with the supplied name
		 */
		case 'b':
			named_boot_file = B_TRUE;
			boot_file = optarg;
			break;
		/*
		 * The name of the service is supplied.
		 */
		case 'n':
			named_service = B_TRUE;
			service_name = optarg;
			break;
		/*
		 * The starting IP address is supplied.
		 */
		case 'i':
			dhcp_setup_needed = B_TRUE;
			ip_start = optarg;
			break;
		/*
		 * Number of IP addresses to be setup
		 */
		case 'c':
			ip_count = atoi(optarg);
			break;
		/*
		 * Source image is supplied.
		 */
		case 's':
			create_netimage = B_TRUE;
			source_path = optarg;
			break;
		/*
		 * DHCP server is remote
		 */
		case 'D':
			use_remote_dhcp_server = B_TRUE;
			dhcp_server = optarg;
			break;
		default:
			(void) fprintf(stderr, "%s\n", gettext(use));
			return (INSTALLADM_FAILURE);
		}
	}

	/*
	 * The last argument is the target directory
	 */
	target_directory = argv[optind++];

	if (target_directory == NULL) {
		(void) fprintf(stderr, "%s\n", gettext(use));
		return (INSTALLADM_FAILURE);
	}

	/*
	 * We don't support DHCP on remote system yet.
	 * So disable DHCP setup
	 */
	if (use_remote_dhcp_server) {
		(void) fprintf(stderr, MSG_REMOTE_DHCP_SETUP);
		dhcp_setup_needed = B_FALSE;
	}
	/*
	 * Check whether target exists
	 * If it doesn't exist, the setup-image script will
	 * create the directory.
	 * If it exists, check whether it has a valid net image
	 */
	if (access(target_directory, F_OK) == 0) {
		if (stat(target_directory, &stat_buf) == 0) {
			char	path[MAXPATHLEN];
			/*
			 * If the directory is empty, then it is okay
			 */
			if (stat_buf.st_nlink > 2) {
				/*
				 * Check whether it has valid file solaris.zlib
				 */
				(void) snprintf(path, sizeof (path), "%s/%s",
				    target_directory,
				    AI_NETIMAGE_REQUIRED_FILE);
				if (access(path, R_OK) != 0) {
					(void) fprintf(stderr,
					    MSG_TARGET_NOT_EMPTY);
					return (INSTALLADM_FAILURE);
				}
				/*
				 * Already have an image. We can't create a
				 * new one w/o removing the old one.
				 * Display error
				 */
				if (create_netimage) {
					(void) fprintf(stderr,
					    MSG_VALID_IMAGE_ERR,
					    target_directory);
					return (INSTALLADM_FAILURE);
				}
			}
		} else {
			(void) fprintf(stderr,
			    MSG_DIRECTORY_ACCESS_ERR,
			    target_directory, errno);
			return (INSTALLADM_FAILURE);
		}
	}

	/*
	 * call the script to create the netimage
	 */
	if (create_netimage) {
		(void) snprintf(cmd, sizeof (cmd), "%s %s %s %s",
		    SETUP_IMAGE_SCRIPT, IMAGE_CREATE,
		    source_path, target_directory);
		if (installadm_system(cmd) != 0) {
			(void) fprintf(stderr, MSG_CREATE_IMAGE_ERR);
			return (INSTALLADM_FAILURE);
		}
	}

	/*
	 * Check whether image is sparc or x86
	 */
	(void) snprintf(mpath, sizeof (mpath), "%s/%s", target_directory,
			"boot/sparc.microroot");
	if (access(mpath, F_OK) == 0) {
		have_sparc = B_TRUE;
	} else {
		(void) snprintf(mpath, sizeof (mpath), "%s/%s",
			target_directory, "boot/x86.microroot");
		if (access(mpath, F_OK) != 0) {
			(void) fprintf(stderr, MSG_MISSING_MICROROOT_ERR);
			return (INSTALLADM_FAILURE);
		}
	}

	/*
	 * The net-image is created, now start the service
	 * If the user provided the name of the service, use it
	 */
	srv_name[0] = '\0';
	if (named_service) {
		int ret;

		snprintf(cmd, sizeof (cmd), "%s %s %s %s %s",
		    SETUP_SERVICE_SCRIPT, SERVICE_LOOKUP,
		    service_name, INSTALL_TYPE, LOCAL_DOMAIN);
		ret = installadm_system(cmd);
		if (ret != 0) {
			create_service = B_TRUE;
		}
		strlcpy(srv_name, service_name, sizeof (srv_name));
	} else {
		/*
		 * The service is not given as input. We will generate
		 * a service name and start the service.
		 */
		create_service = B_TRUE;
	}

	txt_record[0] = '\0';
	if (create_service) {
		char		hostname[256];
		uint16_t	wsport;
		int		ret;

		gethostname(hostname, sizeof (hostname));
		wsport = get_a_free_tcp_port(START_WEB_SERVER_PORT);
		if (wsport == 0) {
			(void) fprintf(stderr, MSG_CANNOT_FIND_PORT);
			return (INSTALLADM_FAILURE);
		}
		snprintf(txt_record, sizeof (txt_record), "%s=%s:%u",
		    AIWEBSERVER, hostname, wsport);
		if (!named_service) {
			snprintf(srv_name, sizeof (srv_name),
			    "_install_service_%u", wsport);
		}
		snprintf(cmd, sizeof (cmd), "%s %s %s %s %s %u %s",
		    SETUP_SERVICE_SCRIPT, SERVICE_REGISTER,
		    srv_name, INSTALL_TYPE,
		    LOCAL_DOMAIN, wsport, txt_record);
		if ((ret = installadm_system(cmd)) != 0) {
			(void) fprintf(stderr,
			    MSG_REGISTER_SERVICE_FAIL, srv_name);
			return (INSTALLADM_FAILURE);
		}
	}

	/*
	 * Setup dhcp
	 */
	if (dhcp_setup_needed && create_netimage) {
		snprintf(cmd, sizeof (cmd), "%s %s %s %d",
		    SETUP_DHCP_SCRIPT, DHCP_SERVER, ip_start, ip_count);
		if (installadm_system(cmd) != 0) {
			(void) fprintf(stderr,
			    MSG_CREATE_DHCP_SERVER_ERR);
			return (INSTALLADM_FAILURE);
		}
	}

	bfile[0] = '\0';
	if (named_boot_file) {
		strlcpy(bfile, boot_file, sizeof (bfile));
	} else {
		char *normalized_service;
		char *ptr;

		normalized_service = strdup(srv_name);
		for (ptr = normalized_service; *ptr != '\0'; ptr++) {
			if (*ptr == ' ') {
				*ptr = '_';
			}
		}
		strlcpy(bfile, normalized_service, sizeof (bfile));
		free(normalized_service);
	}

	if (create_netimage) {
		char	host[256];
		struct	hostent	*hp;
		char	server_ip[128];
		struct in_addr in;
		char	dhcpbfile[MAXPATHLEN];
		char	dhcprpath[MAXPATHLEN];

		if (gethostname(host, sizeof (host)) != 0) {
			(void) fprintf(stderr, MSG_GET_HOSTNAME_FAIL);
			return (INSTALLADM_FAILURE);
		}
		hp = gethostbyname(host);
		if (hp == NULL) {
			(void) fprintf(stderr, MSG_GET_HOSTNAME_FAIL);
			return (INSTALLADM_FAILURE);
		}

		/*
		 * It return addr_list for this host
		 * Pick the first address for now
		 */
		(void) memcpy(&in.s_addr, *hp->h_addr_list, sizeof (in.s_addr));
		snprintf(server_ip, sizeof (server_ip), "%s", inet_ntoa(in));

		snprintf(dhcp_macro, sizeof (dhcp_macro),
		    "dhcp_macro_%s", bfile);

		/*
		 * determine contents of bootfile info passed to dhcp script
		 * as well as rootpath for sparc
		 */
		if (have_sparc) {
			snprintf(dhcpbfile, sizeof (dhcpbfile),
				"http://%s:%s/%s", server_ip, HTTP_PORT,
				WANBOOTCGI);
			snprintf(dhcprpath, sizeof (dhcprpath),
				"http://%s:%s%s", server_ip, HTTP_PORT,
				target_directory);
		} else {
			strlcpy(dhcpbfile, bfile, sizeof (dhcpbfile));
		}

		snprintf(cmd, sizeof (cmd), "%s %s %s %s %s %s %s",
		    SETUP_DHCP_SCRIPT, DHCP_MACRO, have_sparc?"sparc":"x86",
		    server_ip, dhcp_macro, dhcpbfile,
		    have_sparc?dhcprpath:"x86");
		if (installadm_system(cmd) != 0) {
			(void) fprintf(stderr,
			    MSG_ASSIGN_DHCP_MACRO_ERR);
		}
	}

	if (dhcp_setup_needed && create_netimage) {
		snprintf(cmd, sizeof (cmd), "%s %s %s %d %s",
		    SETUP_DHCP_SCRIPT, DHCP_ASSIGN,
		    ip_start, ip_count, dhcp_macro);
		if (installadm_system(cmd) != 0) {
			(void) fprintf(stderr,
			    MSG_ASSIGN_DHCP_MACRO_ERR);
		}
	}

	if (use_remote_dhcp_server) {
		/* handle later */
	}

	/*
	 * Perform sparc/x86 specific actions.
	 */
	if (have_sparc) {
	    /* sparc only */
	    snprintf(cmd, sizeof (cmd), "%s %s %s %s",
		SETUP_SPARC_SCRIPT, SPARC_SERVER, target_directory, srv_name);
	    if (installadm_system(cmd) != 0) {
		(void) fprintf(stderr, MSG_SETUP_SPARC_FAIL);
		return (INSTALLADM_FAILURE);
	    }
	} else {
	    /* x86 only */
	    snprintf(cmd, sizeof (cmd), "%s %s %s %s",
		SETUP_TFTP_LINKS_SCRIPT, srv_name, target_directory, bfile);
	    if (installadm_system(cmd) != 0) {
		(void) fprintf(stderr, MSG_CREATE_TFTPBOOT_FAIL);
		return (INSTALLADM_FAILURE);
	    }
	}

	/*
	 * Register the information about the service, image and boot file
	 * so that it can be used later
	 */
	save_service_data(srv_name, target_directory, bfile, txt_record);
	return (INSTALLADM_SUCCESS);
}

/*
 * do_delete_service:
 * This function stops the DNS-SD service with the given name
 * If the -x argument is passed, it will remove the image, bootfile from
 * /tftpboot
 */
static int
do_delete_service(int argc, char *argv[], const char *use)
{
	char		cmd[MAXPATHLEN];
	char		*service;
	boolean_t	delete_image = B_FALSE;
	int		ret;
	char		directory[MAXPATHLEN];
	char		boot_file[MAXNAMELEN];
	char		txt_record[DATALEN];

	if (argc != 2 && argc != 3) {
		(void) fprintf(stderr, "%s\n", gettext(use));
		return (INSTALLADM_FAILURE);
	}

	if (argc == 3) {
		if (strcmp(argv[1], "-x") != 0) {
			(void) fprintf(stderr, "%s\n", gettext(use));
			return (INSTALLADM_FAILURE);
		}
		delete_image = B_TRUE;
		service = argv[2];
	} else {
		service = argv[1];
	}

	snprintf(cmd, sizeof (cmd), "%s %s %s %s %s",
	    SETUP_SERVICE_SCRIPT, SERVICE_REMOVE,
	    service, INSTALL_TYPE, LOCAL_DOMAIN);
	if ((ret = installadm_system(cmd)) != 0) {
		(void) fprintf(stderr,
		    MSG_REMOVE_SERVICE_FAIL, service);
		return (INSTALLADM_FAILURE);
	}

	if (delete_image) {
		/*
		 * Get the image directory and other things using the service
		 */
		ret = get_service_data(service, directory,
		    boot_file, txt_record);
		if (ret == B_TRUE) {
			(void) snprintf(cmd, sizeof (cmd), "%s %s %s",
			    SETUP_IMAGE_SCRIPT, IMAGE_DELETE,
			    directory);
			if (installadm_system(cmd) != 0) {
				(void) fprintf(stderr,
				    MSG_DELETE_IMAGE_FAIL, directory);
				return (INSTALLADM_FAILURE);
			}
			/*
			 * Delete the service record
			 */
			remove_service_data(service, directory,
			    boot_file, txt_record);
		}
	}
}

/*
 * do_list:
 * List A/I services or print service manifests and criteria
 * Parse the command line for service name; if we do not have one, then
 * print a list of installed services; if we have a service name, get the
 * service directory path from that service name; then pass service directory
 * path to list-manifests(1) (if the internal -c option is provided pass it
 * to list-manifests(1) as well).
 */
static int
do_list(int argc, char *argv[], const char *use)
{
	int		opt;
	char		*port = NULL;
	char		*service_name = NULL;
	char		image_dir[MAXNAMELEN];
	char		boot_file[MAXNAMELEN];
	char		txt_record[DATALEN];
	boolean_t	print_criteria = B_FALSE;
	char		cmd[MAXPATHLEN];
	int		ret;

	/*
	 * The -c option is an internal option
	 */
	while ((opt = getopt(argc, argv, "n:c")) != -1) {
		switch (opt) {
		/*
		 * The name of the service is supplied.
		 */
		case 'n':
			service_name = optarg;
			break;
		case 'c':
			print_criteria = B_TRUE;
			break;
		default:
			(void) fprintf(stderr, "%s\n", gettext(use));
			return (INSTALLADM_FAILURE);
		}
	}

	/*
	 * Make sure correct option combinations
	 */
	if ((print_criteria == B_TRUE) && (service_name == NULL)) {
		(void) fprintf(stderr, MSG_MISSING_OPTIONS, argv[0]);
		(void) fprintf(stderr, "%s\n", gettext(use));
		return (INSTALLADM_FAILURE);
	}

	if (service_name != NULL) {
		/*
		 * Get the list of published manifests from the service
		 */
		/*
		 * Gather the directory location of the service
		 */
		if (get_service_data(service_name, image_dir, boot_file,
		    txt_record) != B_TRUE) {
			(void) fprintf(stderr, MSG_SERVICE_PROP_FAIL);
			return (INSTALLADM_FAILURE);
		}
		/*
		 * txt_record should be of the form
		 * "aiwebserver=<host_ip>:<port>" and the directory location
		 * will be AI_SERVICE_DIR_PATH/<port>
		 */
		port = strrchr(txt_record, ':');

		if (port == NULL) {
			(void) fprintf(stderr, MSG_SERVICE_PROP_FAIL);
			return (INSTALLADM_FAILURE);
		}

		/*
		 * Exclude colon from string (so advance one character)
		 */
		port++;

		/*
		 * Print criteria if requested
		 */
		if (print_criteria == B_TRUE) {
			(void) snprintf(cmd, sizeof (cmd), "%s %s %s%s",
			    MANIFEST_LIST_SCRIPT, "-c", AI_SERVICE_DIR_PATH,
			    port);
		} else {
			(void) snprintf(cmd, sizeof (cmd), "%s %s%s",
			    MANIFEST_LIST_SCRIPT, AI_SERVICE_DIR_PATH,
			    port);
		}

		ret = installadm_system(cmd);

		/*
		 * Ensure we return an error if ret != 0.
		 * If ret == 1 then the Python handled the error, do not print a
		 * new error.
		 */
		if (ret != 0) {
			if (ret == 256) {
				return (INSTALLADM_FAILURE);
			}
			(void) fprintf(stderr, MSG_SUBCOMMAND_FAILED, argv[0]);
			return (INSTALLADM_FAILURE);
		}

	} else {
		/*
		 * Get the list of services running on this system
		 */

		snprintf(cmd, sizeof (cmd), "%s %s %s %s",
		    SETUP_SERVICE_SCRIPT, SERVICE_LIST,
		    INSTALL_TYPE, LOCAL_DOMAIN);
		ret = installadm_system(cmd);
		if (ret != 0) {
			(void) fprintf(stderr, MSG_LIST_SERVICE_FAIL);
			return (INSTALLADM_FAILURE);
		}
	}

	return (INSTALLADM_SUCCESS);
}

/*
 * do_start:
 * do_start will restart the service with the name
 */
static int
do_start(int argc, char *argv[], const char *use)
{
	char		hostname[MAXHOSTNAMELEN];
	uint16_t	wsport;
	int		ret;
	char		*service_name;
	char		txt_record[DATALEN];
	char		cmd[MAXPATHLEN];

	if (argc != 2) {
		(void) fprintf(stderr, "%s\n", gettext(use));
		return (INSTALLADM_FAILURE);
	}
	service_name = argv[1];

	if (gethostname(hostname, sizeof (hostname)) != 0) {
		(void) fprintf(stderr, MSG_GET_HOSTNAME_FAIL);
		return (INSTALLADM_FAILURE);
	}

	wsport = get_a_free_tcp_port(START_WEB_SERVER_PORT);
	if (wsport == 0) {
		(void) fprintf(stderr, MSG_CANNOT_FIND_PORT);
		return (INSTALLADM_FAILURE);
	}
	/*
	 * Currently start is same as registering a service
	 */
	snprintf(txt_record, sizeof (txt_record), "%s=%s:%u",
	    AIWEBSERVER, hostname, wsport);
	snprintf(cmd, sizeof (cmd), "%s %s %s %s %s %u %s",
	    SETUP_SERVICE_SCRIPT, SERVICE_REGISTER,
	    service_name, INSTALL_TYPE,
	    LOCAL_DOMAIN, wsport, txt_record);
	if ((ret = installadm_system(cmd)) != 0) {
		(void) fprintf(stderr, MSG_REGISTER_SERVICE_FAIL,
		    service_name);
		return (INSTALLADM_FAILURE);
	}
	return (INSTALLADM_SUCCESS);
}

/*
 * do_stop:
 * do_stop will stop (delete) the service with the name
 */
static int
do_stop(int argc, char *argv[], const char *use)
{
	char		cmd[MAXPATHLEN];
	char		*service;
	int		ret;

	if (argc != 2) {
		(void) fprintf(stderr, "%s\n", gettext(use));
		return (INSTALLADM_FAILURE);
	}

	service = argv[1];

	/*
	 * Currently stop is same as removing service
	 */
	snprintf(cmd, sizeof (cmd), "%s %s %s %s %s",
	    SETUP_SERVICE_SCRIPT, SERVICE_REMOVE,
	    service, INSTALL_TYPE, LOCAL_DOMAIN);
	if ((ret = installadm_system(cmd)) != 0) {
		(void) fprintf(stderr,
		    MSG_REMOVE_SERVICE_FAIL, service);
		return (INSTALLADM_FAILURE);
	}
}

static int
do_create_client(int argc, char *argv[], const char *use)
{

	int	option;
	int	ret;
	char	*protocol = NULL;
	char	*mac_addr = NULL;
	char	*bootargs = NULL;
	char	*imagepath = NULL;
	char	*svcname = NULL;

	while ((option = getopt(argc, argv, ":P:b:e:n:t:")) != -1) {
		switch (option) {
		case 'b':
			bootargs = optarg;
			break;
		case 'e':
			mac_addr = optarg;
			break;
		case 'n':
			svcname = optarg;
			break;
		case 'P':
			protocol = optarg;
			break;
		case 't':
			imagepath = optarg;
			break;
		default:
			do_opterr(optopt, option, use);
			return (INSTALLADM_FAILURE);
		}
	}

	/*
	 * Make sure required options are there
	 */
	if ((mac_addr == NULL) || (svcname == NULL) || (imagepath == NULL)) {
		(void) fprintf(stderr, MSG_MISSING_OPTIONS, argv[0]);
		(void) fprintf(stderr, "%s\n", gettext(use));
		return (INSTALLADM_FAILURE);
	}

	ret = call_script(CREATE_CLIENT_SCRIPT, argc-1, &argv[1]);
	if (ret != 0) {
		return (INSTALLADM_FAILURE);
	}
	return (INSTALLADM_SUCCESS);
}


static int
do_delete_client(int argc, char *argv[], const char *use)
{
	int	ret;

	/*
	 * There is one required argument, mac_addr of client
	 */
	if (argc != 2) {
		(void) fprintf(stderr, "%s\n", gettext(use));
		return (INSTALLADM_FAILURE);
	}

	ret = call_script(DELETE_CLIENT_SCRIPT, argc-1, &argv[1]);
	if (ret != 0) {
		return (INSTALLADM_FAILURE);
	}
	return (INSTALLADM_SUCCESS);
}

/*
 * do_add:
 * Add manifests to an A/I service
 * Parse command line for criteria manifest and service name; get service
 * directory path from service name; then pass manifest and service directory
 * path to publish-manifest(1)
 */
static int
do_add(int argc, char *argv[], const char *use)
{
	int	option = NULL;
	char	*port = NULL;
	char	*manifest = NULL;
	char	*svcname = NULL;
	char	image_dir[MAXNAMELEN];
	char	boot_file[MAXNAMELEN];
	char	txt_record[DATALEN];
	char	cmd[MAXPATHLEN];
	int	ret;

	/*
	 * Check for valid number of arguments
	 */
	if (argc != 5) {
		(void) fprintf(stderr, "%s\n", gettext(use));
		return (INSTALLADM_FAILURE);
	}

	while ((option = getopt(argc, argv, ":n:m:")) != -1) {
		switch (option) {
			case 'n':
				svcname = optarg;
				break;
			case 'm':
				manifest = optarg;
				break;
			default:
				do_opterr(optopt, option, use);
				return (INSTALLADM_FAILURE);
		}
	}

	/*
	 * Make sure required options are there
	 */
	if ((svcname == NULL) || (manifest == NULL)) {
		(void) fprintf(stderr, MSG_MISSING_OPTIONS, argv[0]);
		(void) fprintf(stderr, "%s\n", gettext(use));
		return (INSTALLADM_FAILURE);
	}

	/*
	 * Gather the directory location of the service
	 */
	if (get_service_data(svcname, image_dir, boot_file, txt_record) !=
	    B_TRUE) {
		(void) fprintf(stderr, MSG_SERVICE_PROP_FAIL);
		return (INSTALLADM_FAILURE);
	}
	/*
	 * txt_record should be of the form "aiwebserver=<host_ip>:<port>"
	 * and the directory location will be AI_SERVICE_DIR_PATH/<port>
	 */
	port = strrchr(txt_record, ':');

	if (port == NULL) {
		(void) fprintf(stderr, MSG_SERVICE_PROP_FAIL);
		return (INSTALLADM_FAILURE);
	}
	/*
	 * Exclude colon from string (so advance one character)
	 */
	port++;
	(void) snprintf(cmd, sizeof (cmd), "%s %s %s %s%s",
	    MANIFEST_MODIFY_SCRIPT, "-c",
	    manifest, AI_SERVICE_DIR_PATH, port);

	ret = installadm_system(cmd);

	/*
	 * Ensure we return an error if ret != 0.
	 * If ret == 1 then the Python handled the error, do not print a
	 * new error.
	 */
	if (ret != 0) {
		if (ret == 256) {
			return (INSTALLADM_FAILURE);
		}
		(void) fprintf(stderr, MSG_SUBCOMMAND_FAILED, argv[0]);
		return (INSTALLADM_FAILURE);
	}
	return (INSTALLADM_SUCCESS);
}

/*
 * do_remove:
 * Remove manifests from an A/I service
 * Parse the command line for service name and manifest name (and if provided,
 * internal instance name); then, get the service directory path from the
 * provided service name; then pass the manifest name (instance name if
 * provided) and service directory path to delete-manifest(1)
 */
static int
do_remove(int argc, char *argv[], const char *use)
{
	int	option;
	char	*port = NULL;
	char	*manifest = NULL;
	char	*instance = NULL;
	char	*svcname = NULL;
	char	image_dir[MAXNAMELEN];
	char	boot_file[MAXNAMELEN];
	char	txt_record[DATALEN];
	char	cmd[MAXPATHLEN];
	int	ret;

	/*
	 * Check for valid number of arguments
	 */
	if (argc != 5 && argc != 7) {
		(void) fprintf(stderr, "%s\n", gettext(use));
		return (INSTALLADM_FAILURE);
	}

	/*
	 * The -i option is an internal option
	 */
	while ((option = getopt(argc, argv, ":n:m:i")) != -1) {
		switch (option) {
			case 'n':
				svcname = optarg;
				break;
			case 'm':
				manifest = optarg;
				break;
			case 'i':
				instance = optarg;
				break;
			default:
				do_opterr(optopt, option, use);
				return (INSTALLADM_FAILURE);
		}
	}

	/*
	 * Make sure required options are there
	 */
	if ((svcname == NULL) || (manifest == NULL)) {
		(void) fprintf(stderr, MSG_MISSING_OPTIONS, argv[0]);
		(void) fprintf(stderr, "%s\n", gettext(use));
		return (INSTALLADM_FAILURE);
	}

	/*
	 * Gather the directory location of the service
	 */
	if (get_service_data(svcname, image_dir, boot_file, txt_record) !=
	    B_TRUE) {
		(void) fprintf(stderr, MSG_SERVICE_PROP_FAIL);
		return (INSTALLADM_FAILURE);
	}
	/*
	 * txt_record should be of the form "aiwebserver=<host_ip>:<port>"
	 * and the directory location will be AI_SERVICE_DIR_PATH/<port>
	 */
	port = strrchr(txt_record, ':');

	if (port == NULL) {
		(void) fprintf(stderr, MSG_SERVICE_PROP_FAIL);
		return (INSTALLADM_FAILURE);
	}
	/*
	 * Exclude colon from string (so advance one character)
	 */
	port++;

	/*
	 * See if we're removing a single instance or a whole manifest
	 */
	if (instance == NULL) {
		(void) snprintf(cmd, sizeof (cmd), "%s %s %s%s",
		    MANIFEST_REMOVE_SCRIPT,
		    manifest, AI_SERVICE_DIR_PATH, port);
	} else {
		(void) snprintf(cmd, sizeof (cmd), "%s %s %s %s %s%s",
		    MANIFEST_REMOVE_SCRIPT,
		    manifest, "-i", instance,
		    AI_SERVICE_DIR_PATH, port);
	}
	ret = installadm_system(cmd);

	/*
	 * Ensure we return an error if ret != 0.
	 * If ret == 1 then the Python handled the error, do not print a
	 * new error.
	 */
	if (ret != 0) {
		if (ret == 256) {
			return (INSTALLADM_FAILURE);
		}
		(void) fprintf(stderr, MSG_SUBCOMMAND_FAILED, argv[0]);
		return (INSTALLADM_FAILURE);
	}

	return (INSTALLADM_SUCCESS);
}


static int
do_set(int argc, char *argv[], const char *use)
{
}


static int
do_version(int argc, char *argv[], const char *use)
{
	(void) fprintf(stdout, MSG_INSTALLADM_VERSION,
	    progname, INSTALLADM_VERSION);
	return (INSTALLADM_SUCCESS);
}


static int
do_help(int argc, char *argv[], const char *use)
{
	int	i;
	int	numcmds;
	cmd_t	*cmdp;

	if (argc == 1) {
		usage();
		return (INSTALLADM_FAILURE);
	}

	numcmds = sizeof (cmds) / sizeof (cmds[0]);
	for (i = 0; i < numcmds; i++) {
		cmdp = &cmds[i];
		if (strcmp(argv[1], cmdp->c_name) == 0) {
			if (cmdp->c_usage != NULL) {
				(void) fprintf(stdout, "%s\n",
				    gettext(cmdp->c_usage));
			} else {
				(void) fprintf(stdout,
				    MSG_OPTION_NOHELP, progname,
				    argv[0], cmdp->c_name);
			}
			return (INSTALLADM_SUCCESS);
		}
	}

	(void) fprintf(stderr, MSG_UNKNOWN_HELPSUBCOMMAND,
	    progname, argv[0], argv[1]);
	usage();
	return (INSTALLADM_FAILURE);
}


static void
do_opterr(int opt, int opterr, const char *usage)
{
	switch (opterr) {
		case ':':
			(void) fprintf(stderr,
			    MSG_OPTION_VALUE_MISSING, opt, gettext(usage));
		break;
		case '?':
		default:
			(void) fprintf(stderr,
			    MSG_OPTION_UNRECOGNIZED, opt, gettext(usage));
		break;
	}
}

/*
 * get_a_free_tcp_port
 * This returns the next available tcp port
 *
 * Input:
 * uint16_t start	- Find a free port starting from this port
 *
 * Output:
 * uint16_t start	- An unused port
 */
static uint16_t
get_a_free_tcp_port(uint16_t start)
{
	uint16_t port;
	int	sock;
	struct sockaddr_in addr;

	port = start;

	sock = socket(AF_INET, SOCK_STREAM, 0);
	if (sock < 0) {
		return (0);
	}

	addr.sin_addr.s_addr = INADDR_ANY;
	addr.sin_family = AF_INET;
	addr.sin_port = htons(port);
	while (bind(sock, (struct sockaddr *)&addr, sizeof (addr)) < 0) {
		port++;
		addr.sin_port = htons(port);
	}

	/*
	 * Now close the socket and use the port
	 */
	close(sock);
	return (port);
}

/*
 * get_service_data
 * Find the information about the service passed as the first parameter
 *
 * Input:
 * char *service	- Name of the service
 *
 * Output:
 * char * image_dir	- The full pathname of the net image used for
 *			  this service
 * char * boot_file	- The name of the file used to create /tftpboot links
 *			  The DHCP server will send the name to the client
 *			  and the client will use this name to get the net image
 * char * txt_record	- The data that is passed to the mDNS service
 *			  It looks like "aiwebserver=<host_ip>:<port>"
 * Return:
 * B_TRUE		- If the service is found
 * B_FALSE		- If the service cannot be found
 */
static boolean_t
get_service_data(char *service, char *image_dir,
	char *boot_file, char *txt_record)
{
	FILE	*fp;
	char	service_name[DATALEN];
	char	buf[1024];
	char	*token;

	service_name[0] = '\0';

	if (access(AI_SERVICE_DATA, F_OK) != 0) {
		return (B_FALSE);
	}

	fp = fopen(AI_SERVICE_DATA, "r");
	if (fp == NULL) {
		(void) fprintf(stderr, MSG_SERVICE_DATA_FILE_FAIL,
		    AI_SERVICE_DATA);
		return (B_FALSE);
	}

	while (fgets(buf, sizeof (buf), fp) != NULL) {
		token = strtok(buf, ";\n");
		if (token != NULL) {
			(void) strlcpy(service_name, token,
			    sizeof (service_name));
		}

		/*
		 * Found the service name
		 */
		if (strcmp(service_name, service) == 0) {
			token = strtok(NULL, ";\n");
			if (token != NULL) {
				(void) strlcpy(image_dir, token, MAXPATHLEN);
			}
			token = strtok(NULL, ";\n");
			if (token != NULL) {
				(void) strlcpy(boot_file, token, MAXNAMELEN);
			}
			token = strtok(NULL, ";\n");
			if (token != NULL) {
				(void) strlcpy(txt_record, token, DATALEN);
			}
			fclose(fp);
			return (B_TRUE);

		}
	}

	fclose(fp);
	return (B_FALSE);
}

/*
 * remove_service_data
 * All the information about a service is to be removed from the service file
 *
 * Input:
 * char *service	- Name of the service
 * char * image_dir	- The full pathname of the net image used for
 *			  this service
 * char * boot_file	- The name of the file used to create /tftpboot links
 *			  The DHCP server will send the name to the client
 *			  and the client will use this name to get the net image
 * char * txt_record	- The data that is passed to the mDNS service
 *			  It looks like "aiwebserver=<host_ip>:<port>"
 */
static void
remove_service_data(char *service, char *image_dir,
	char *boot_file, char *txt_record)
{
	FILE	*fp, *tmp_fp;
	char	service_name[DATALEN];
	char	directory[MAXPATHLEN];
	char	file[MAXNAMELEN];
	char	data[DATALEN];
	char	tmp_file[MAXPATHLEN];
	char	buf[MAX_SERVICE_LINE_LEN];
	char	line[MAX_SERVICE_LINE_LEN];
	char	*token;

	service_name[0] = '\0';
	directory[0] = '\0';
	file[0] = '\0';
	data[0] = '\0';

	/*
	 * If the file doesn't exist, there is nothing to remove
	 */
	if (access(AI_SERVICE_DATA, F_OK) != 0) {
		return;
	}

	/*
	 * We need to remove the service from our database
	 * Read the database file and write all the lines except the one
	 * we want to delete on to a temporary file.
	 * At the end, we can swap the temporary file to  our database
	 */
	(void) snprintf(tmp_file, sizeof (tmp_file), "%s.%d",
	    "/var/tmp/installadm", getpid());
	tmp_fp = fopen(tmp_file, "w");
	if (tmp_fp == NULL) {
		(void) fprintf(stderr, MSG_SERVICE_DATA_FILE_FAIL,
		    tmp_file);
		return;
	}

	fp = fopen(AI_SERVICE_DATA, "r");
	if (fp == NULL) {
		(void) fprintf(stderr, MSG_SERVICE_DATA_FILE_FAIL,
		    AI_SERVICE_DATA);
		fclose(tmp_fp);
		unlink(tmp_file);
		return;
	}

	while (fgets(line, sizeof (line), fp) != NULL) {
		/*
		 * We need to write the line if it doesn't have the service
		 * we are looking for. So save the line before using strtok
		 */
		strlcpy(buf, line, sizeof (buf));
		token = strtok(line, ";\n");
		if (token != NULL) {
			(void) strlcpy(service_name, token,
			    sizeof (service_name));
		}
		token = strtok(NULL, ";\n");
		if (token != NULL) {
			(void) strlcpy(directory, token, sizeof (directory));
		}
		token = strtok(NULL, ";\n");
		if (token != NULL) {
			(void) strlcpy(file, token, sizeof (file));
		}
		token = strtok(NULL, ";\n");
		if (token != NULL) {
			(void) strlcpy(data, token, sizeof (data));
		}

		/*
		 * If the service name or target directory is different
		 * add the service entry to the temporary file
		 * Do not add the line if service and target directory
		 * are matching
		 */
		if ((strcmp(service_name, service) != 0) ||
		    (strcmp(directory, image_dir) != 0))  {
			fputs(buf, tmp_fp);
		}
	}

	fclose(fp);
	fclose(tmp_fp);

	if (rename(tmp_file, AI_SERVICE_DATA) < 0) {
		perror("rename");
	}
}

/*
 * save_service_data
 * All the information about a service is added to the service file
 *
 * Input:
 * char *service	- Name of the service
 * char * image_dir	- The full pathname of the net image used for
 *			  this service
 * char * boot_file	- The name of the file used to create /tftpboot links
 *			  The DHCP server will send the name to the client
 *			  and the client will use this name to get the net image
 * char * txt_record	- The data that is passed to the mDNS service
 *			  It looks like "aiwebserver=<host_ip>:<port>"
 */
static void
save_service_data(char *service, char *image_dir,
	char *boot_file, char *txt_record)
{
	FILE	*fp;
	char	*str;
	int	size;

	if (service == NULL || image_dir == NULL ||
	    boot_file == NULL || txt_record == NULL) {
		return;
	}

	if (access(AI_SERVICE_DATA, F_OK) == 0) {
		remove_service_data(service, image_dir, boot_file, txt_record);
	}

	fp = fopen(AI_SERVICE_DATA, "a");
	if (fp == NULL) {
		(void) fprintf(stderr, MSG_SERVICE_DATA_FILE_FAIL,
		    AI_SERVICE_DATA);
		return;
	}

	/*
	 * The service record is of the format
	 * service;image_dir;boot_file;txt_record
	 */
	size = strlen(service) + strlen(image_dir) +
	    strlen(boot_file) + strlen(txt_record) + 6;
	str = (char *)malloc(size);
	if (str == NULL) {
		return;
	}
	(void) snprintf(str, size, "%s;%s;%s;%s\n",
	    service, image_dir, boot_file, txt_record);
	fputs(str, fp);
	fclose(fp);
	free(str);
}

/*
 * installadm_system()
 *
 * Function to execute shell commands in a thread-safe manner
 * Parameters:
 *	cmd - the command to execute
 * Return:
 *	return code from command
 *	if popen() fails, -1
 * Status:
 *	private
 */
static	int
installadm_system(char *cmd)
{
	FILE	*p;

	if ((p = popen(cmd, "w")) == NULL)
		return (-1);

	return (pclose(p));
}
