"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals

import re
import os
import logging
import datetime
import subprocess

from osbs.exceptions import OsbsException

logger = logging.getLogger(__name__)

KLIST_TGT_RE = (r"\d\d/\d\d/\d{2,4}"
                r" +"
                r"\d\d:\d\d:\d\d"
                r" +"
                r"(?P<month>\d\d)"
                r"/"
                r"(?P<day>\d\d)"
                r"/"
                r"(?P<year>\d{2,4})"
                r" +"
                r"(?P<hour>\d\d)"
                r":"
                r"(?P<minute>\d\d)"
                r":"
                r"(?P<second>\d\d)"
                r" +"
                r"krbtgt/(?P<realm>[-.A-Z0-9]+)@(?P=realm)")


def run(cmd, extraenv=None):
    env = os.environ.copy()
    if extraenv:
        env.update(extraenv)

    logger.debug("Subprocess: %s", ' '.join(cmd))
    # universal_newlines=True causes stdout/stderr to be strings (not bytes) in py3
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                         universal_newlines=True)
    stdout, stderr = p.communicate()

    return p.returncode, stdout, stderr


def kerberos_ccache_init(principal, keytab_file, ccache_file=None):
    """
    Checks whether kerberos credential cache has ticket-granting ticket that is valid for at least
    an hour.

    Default ccache is used unless ccache_file is provided. In that case, KRB5CCNAME environment
    variable is set to the value of ccache_file if we successfully obtain the ticket.
    """
    tgt_valid = False
    env = {"LC_ALL": "C"}  # klist uses locales to format date on RHEL7+
    if ccache_file:
        env["KRB5CCNAME"] = ccache_file

    # check if we have tgt that is valid more than one hour
    rc, klist, _ = run(["klist"], extraenv=env)
    if rc == 0:
        for line in klist.splitlines():
            m = re.match(KLIST_TGT_RE, line)
            if m:
                year = m.group("year")
                if len(year) == 2:
                    year = "20" + year

                expires = datetime.datetime(
                    int(year), int(m.group("month")), int(m.group("day")),
                    int(m.group("hour")), int(m.group("minute")), int(m.group("second"))
                )

                if expires - datetime.datetime.now() > datetime.timedelta(hours=1):
                    logger.debug("Valid TGT found, not renewing")
                    tgt_valid = True
                    break

    if not tgt_valid:
        logger.debug("Retrieving kerberos TGT")
        rc, out, err = run(["kinit", "-k", "-t", keytab_file, principal], extraenv=env)
        if rc != 0:
            raise OsbsException("kinit returned %s:\nstdout: %s\nstderr: %s" % (rc, out, err))

    if ccache_file:
        os.environ["KRB5CCNAME"] = ccache_file
