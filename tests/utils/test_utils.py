"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import

from flexmock import flexmock
import os
import os.path
import subprocess
import pytest
import datetime
import re
import sys
import requests
from time import tzset
from pkg_resources import parse_version
from textwrap import dedent

from osbs.constants import REPO_CONTAINER_CONFIG
from osbs.repo_utils import RepoInfo
from osbs.utils import (buildconfig_update,
                        get_imagestreamtag_from_image,
                        git_repo_humanish_part_from_uri, sanitize_strings_for_openshift,
                        get_time_from_rfc3339, strip_registry_from_image,
                        TarWriter, TarReader, make_name_from_git, wrap_name_from_git,
                        get_instance_token_file_name, sanitize_version,
                        has_triggers, strip_registry_and_tag_from_image,
                        clone_git_repo, get_repo_info, ImageName)
from osbs.exceptions import OsbsException, OsbsCommitNotFound
from tests.constants import (TEST_DOCKERFILE_GIT, TEST_DOCKERFILE_SHA1, TEST_DOCKERFILE_INIT_SHA1,
                             TEST_DOCKERFILE_BRANCH)
import osbs.kerberos_ccache


BC_NAME_REGEX = r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$'
BC_LABEL_REGEX = r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?([\/\.]*[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$'

TEST_DATA = {
    "repository.com/image-name:latest": ImageName(registry="repository.com", repo="image-name"),
    "repository.com/prefix/image-name:1": ImageName(registry="repository.com",
                                                    namespace="prefix",
                                                    repo="image-name", tag="1"),
    "repository.com/prefix/image-name@sha256:12345": ImageName(registry="repository.com",
                                                               namespace="prefix",
                                                               repo="image-name",
                                                               tag="sha256:12345"),
    "repository.com/prefix/image-name:latest": ImageName(registry="repository.com",
                                                         namespace="prefix",
                                                         repo="image-name"),
    "image-name:latest": ImageName(repo="image-name"),

    "registry:5000/image-name@sha256:12345": ImageName(registry="registry:5000",
                                                       repo="image-name", tag="sha256:12345"),
    "registry:5000/image-name:latest": ImageName(registry="registry:5000", repo="image-name"),

    "fedora:20": ImageName(repo="fedora", tag="20"),
    "fedora@sha256:12345": ImageName(repo="fedora", tag="sha256:12345"),

    "prefix/image-name:1": ImageName(namespace="prefix", repo="image-name", tag="1"),
    "prefix/image-name@sha256:12345": ImageName(namespace="prefix", repo="image-name",
                                                tag="sha256:12345"),

    "library/fedora:20": ImageName(namespace="library", repo="fedora", tag="20"),
    "library/fedora@sha256:12345": ImageName(namespace="library", repo="fedora",
                                             tag="sha256:12345"),
}


def has_connection():
    try:
        requests.get("https://github.com/")
        return True
    except requests.ConnectionError:
        return False


# In case we run tests in an environment without internet connection.
requires_internet = pytest.mark.skipif(not has_connection(), reason="requires internet connection")


def test_buildconfig_update():
    x = {'a': 'a', 'strategy': {'b1': 'B1', 'b2': 'B2', 'b11': {'x': 'y'}}, 'd': 'D'}
    y = {'a': 'A', 'strategy': {'b1': 'newB1', 'b3': 'B3', 'b11': {}}, 'c': 'C'}
    buildconfig_update(x, y)
    assert x == {'a': 'A', 'strategy': {'b1': 'newB1', 'b3': 'B3', 'b11': {}}, 'c': 'C', 'd': 'D'}


def has_trigger(buildconfig, trigger_type):
    if not has_triggers(buildconfig):
        return False
    triggers = buildconfig['spec']['triggers']
    if not triggers:
        return False
    for t in triggers:
        if t.get('type') == trigger_type:
            return True


def add_ist(buildconfig):
    buildconfig['spec'].setdefault('triggers', [])
    buildconfig['spec']['triggers'].append({
        "type": "ImageChange",
        "imageChange": {
            "from": {
                "kind": "ImageStreamTag",
                "name": "foobar",
            }
        }
    })


def add_other(buildconfig):
    buildconfig['spec'].setdefault('triggers', [])
    buildconfig['spec']['triggers'].append({
            "type": "Generic",
            "generic": {
                "secret": "secret101",
                "allowEnv": True,
            }
        }
    )


@pytest.mark.parametrize(('orig_ist', 'orig_other'), [
    (True, False),
    (False, True),
    (True, True)
])
def test_trigger_removals(orig_ist, orig_other):
    orig = {
        'spec': {
            'foo': 'bar',
            }
        }

    new = {
        'spec': {
            'baz': 'qux',
        }
    }

    if orig_ist:
        add_ist(orig)
    if orig_other:
        add_other(orig)

    buildconfig_update(orig, new)

    if orig_other:
        assert has_trigger(orig, 'Generic')
    assert not has_trigger(orig, 'ImageChange')


@pytest.mark.parametrize(('uri', 'humanish'), [
    ('http://git.example.com/git/repo.git/', 'repo'),
    ('http://git.example.com/git/repo.git', 'repo'),
    ('http://git.example.com/git/repo/.git', 'repo'),
    ('git://hostname/path', 'path'),
])
def test_git_repo_humanish_part_from_uri(uri, humanish):
    assert git_repo_humanish_part_from_uri(uri) == humanish


@pytest.mark.parametrize(('img', 'expected'), [
    ('fedora23', 'fedora23'),
    ('fedora23:sometag', 'fedora23:sometag'),
    ('fedora23/python', 'fedora23/python'),
    ('fedora23/python:sometag', 'fedora23/python:sometag'),
    ('docker.io/fedora23', 'fedora23'),
    ('docker.io/fedora23/python', 'fedora23/python'),
    ('docker.io/fedora23/python:sometag', 'fedora23/python:sometag'),
])
def test_strip_registry_from_image(img, expected):
    assert strip_registry_from_image(img) == expected


@pytest.mark.parametrize(('img', 'expected'), [
    ('fedora23', 'fedora23:latest'),
    ('fedora23:sometag', 'fedora23:sometag'),
    ('fedora23/python', 'fedora23-python:latest'),
    ('fedora23/python:sometag', 'fedora23-python:sometag'),
    ('docker.io/fedora23', 'fedora23:latest'),
    ('docker.io/fedora23/python', 'fedora23-python:latest'),
    ('docker.io/fedora23/python:sometag', 'fedora23-python:sometag'),
])
def test_get_imagestreamtag_from_image(img, expected):
    assert get_imagestreamtag_from_image(img) == expected


@pytest.mark.parametrize('tz', [
    'UTC',
    'EST',
])
@pytest.mark.parametrize(('rfc3339', 'seconds'), [
    ('2015-08-24T10:41:00Z', 1440412860.0),
])
def test_get_time_from_rfc3339_valid(rfc3339, seconds, tz):
    os.environ['TZ'] = tz
    tzset()
    assert get_time_from_rfc3339(rfc3339) == seconds


@pytest.mark.parametrize(('str1', 'str2', 'separator', 'limit', 'label', 'expected'), [
    ('spam', 'bacon', '-', 10, True, 'spam-bacon'),
    ('spam', 'bacon', '-', 5, True, 'sp-ba'),
    ('https://github.com/blah/my_very_very_very_long_and_broken_a$$_repo.git', 'bacon', '-', 65,
     True, 'httpsgithub.comblahmyveryveryverylongandbrokenarepo.git-bacon'),
    ('myveryveryveryveryveryveryveryveryveryveryveryveryverylongtestcase', '', '-', 65,
     True, 'myveryveryveryveryveryveryveryveryveryveryveryveryverylongtest'),
    ('https://github.com/blah/my_very_very_very_long_and_broken_a$$_repo.git', 'bacon', '-', 65,
     False, 'httpsgithubcomblahmyveryveryverylongandbrokenarepogit-bacon'),
    ('myveryveryveryveryveryveryveryveryveryveryveryveryverylongtestcase', '', '-', 65,
     False, 'myveryveryveryveryveryveryveryveryveryveryveryveryverylongtest'),
])
def test_sanitize_string(str1, str2, limit, separator, label, expected):
    sanitized = sanitize_strings_for_openshift(str1, str2, limit, separator, label)
    assert sanitized == expected
    valid = re.compile(BC_LABEL_REGEX) if label else re.compile(BC_NAME_REGEX)
    assert valid.match(sanitized)


@pytest.mark.parametrize(('repo', 'branch', 'limit', 'separator', 'expected'), [
    ('spam', 'bacon', 10, '-', 'spam-bacon'),
    ('spam', 'bacon', 5, '-', 'sp-ba'),
    ('spam', 'bacon', 10, 'x', 'spamxbacon'),
    ('spammmmmm', 'bacon', 10, '-', 'spamm-baco'),
    ('spam', 'baconnnnnnn', 10, '-', 'spam-bacon'),
    ('s', 'bacon', 10, '-', 's-bacon'),
    ('spam', 'b', 10, '-', 'spam-b'),
    ('spam', 'bacon', 10, 'x', 'spamxbacon'),
    ('spam', '', 10, '-', 'spam-unkno'),
    ('spam', 'baco-n', 10, '-', 'spam-baco'),
    ('spam', 'ba---n', 10, '-', 'spam-ba'),
    ('spam', '-----n', 10, '-', 'spam'),
    ('https://github.com/blah/spam.git', 'bacon', 10, '-', 'spam-bacon'),
    ('1.2.3.4.5', '6.7.8.9', 10, '-', '12345-6789'),
    ('1.2.3.4.', '...', 10, '-', '1234'),
    ('1.2.3.4.', '...f.', 10, '-', '1234-f'),
    ('1_2_3_4_5', '6_7_8_9', 10, '-', '12345-6789'),
    ('1_2_3_4_', '_f_', 10, '-', '1234-f'),
    ('longer-name-than', 'this', 22, '-', 'longer-name-than-this'),
    ('longer--name--than', 'this', 22, '-', 'longer--name--tha-this'),
    ('https://github.com/blah/my_very_very_very_long_and_broken_a$$_repo.git',
     'bacon', 65, '-', 'myveryveryverylongandbrokenarepo-bacon'),
    ('https://github.com/blah/my_very_very_very_long_and_broken_a$$_repo.git', '',
     65, '-', 'myveryveryverylongandbrokenarepo-unknown'),

])
def test_make_name_from_git(repo, branch, limit, separator, expected, hash_size=5):
    bc_name = make_name_from_git(repo, branch, limit + len(separator) + hash_size, separator,
                                 hash_size=hash_size)

    assert expected == bc_name[:-(hash_size + len(separator))]

    # Is this a valid name for OpenShift to use?
    valid = re.compile(BC_NAME_REGEX)
    assert valid.match(bc_name)


@pytest.mark.parametrize(('prefix', 'expected_prefix'), (
    ('prefix', 'prefix'),
    ('p.r.e.f.i.x', 'prefix'),
    ('p-r-e-f-i-x', 'p-r-e-f-i-x'),
))
@pytest.mark.parametrize(('suffix', 'expected_suffix'), (
    ('suffix', 'suffix'),
    ('s.u.f.f.i.x', 'suffix'),
    ('s-u-f-f-i-x', 's-u-f-f-i-x'),
))
def test_wrap_name_from_git(prefix, expected_prefix, suffix, expected_suffix):
    repo = 'repo'
    branch = 'branch'
    hash_size = 5
    # Amount of separators when joining all segments:
    #    repo, branch, hash, prefix, suffix
    num_separators = 4
    limit = len(suffix) + len(prefix) + hash_size + num_separators
    bc_name = wrap_name_from_git(prefix, suffix, repo, branch, limit=limit, hash_size=hash_size)

    assert bc_name.startswith(expected_prefix + '-')
    assert bc_name.endswith('-' + expected_suffix)

    assert len(bc_name) <= limit

    # Is this a valid name for OpenShift to use?
    valid = re.compile(BC_NAME_REGEX)
    assert valid.match(bc_name)


def test_make_name_from_git_collide():
    bc1 = make_name_from_git("very_log_repo name_first", "also_long_branch_name", 30, '-')
    bc2 = make_name_from_git("very_log_repo name_second", "also_long_branch_name", 30, '-')
    assert bc1 != bc2


SHA_INPUT_FILE = 'tests/input_for_sha.txt'


def test_make_name_from_git_all_from_file():

    all_sha = set()
    with open(SHA_INPUT_FILE) as f:
        lines = f.read().splitlines()

    for line in lines:
        repo, branch = line.split()
        bc_name = make_name_from_git(repo, branch, 30, '-')
        all_sha.add(bc_name)

        # Is this a valid name for OpenShift to use?
        valid = re.compile(BC_NAME_REGEX)
        assert valid.match(bc_name)

    assert len(lines) == len(all_sha)


@pytest.mark.skipif(sys.version_info[0] < 3,
                    reason="requires python3")
@pytest.mark.parametrize('tz', [
    'UTC',
    'EST',
])
@pytest.mark.parametrize(('rfc3339', 'seconds'), [
    # These tests only work in Python 3
    ('2015-08-24T10:41:00.1Z', 1440412860.1),
    ('2015-09-22T11:12:00+01:00', 1442916720),
])
def test_get_time_from_rfc3339_valid_alt_format(rfc3339, seconds, tz):
    os.environ['TZ'] = tz
    tzset()
    assert get_time_from_rfc3339(rfc3339) == seconds


@pytest.mark.parametrize('rfc3339', [
    ('just completely invalid'),
])
def test_get_time_from_rfc3339_invalid(rfc3339):
    with pytest.raises(ValueError):
        get_time_from_rfc3339(rfc3339)


KLIST_TEMPLATE = """
Ticket cache: FILE:/tmp/krb5cc_1000
Default principal: user@REDBAT.COM

Valid starting     Expires            Service principal
08/11/15 08:43:56  %m/%d/%y %H:%M:%S  krbtgt/REDBAT.COM@REDBAT.COM
08/11/15 14:13:19  08/12/15 00:13:14  imap/gmail.org@REDBAT.COM
"""

KEYTAB_PATH = '/etc/keytab'
CCACHE_PATH = '/tmp/krb5cc_thing'
PRINCIPAL = 'prin@IPAL'


@pytest.mark.parametrize("custom_ccache", [True, False])
def test_kinit_nocache(custom_ccache):
    flexmock(osbs.kerberos_ccache).should_receive('run') \
                                  .with_args(['klist'], extraenv=object) \
                                  .and_return(1, "", "") \
                                  .once()
    flexmock(osbs.kerberos_ccache).should_receive('run') \
                                  .with_args(['kinit', '-k', '-t',
                                              KEYTAB_PATH, PRINCIPAL],
                                             extraenv=object) \
                                  .and_return(0, "", "") \
                                  .once()
    flexmock(os.environ).should_receive('__setitem__') \
                        .with_args("KRB5CCNAME", CCACHE_PATH) \
                        .times(1 if custom_ccache else 0)

    osbs.kerberos_ccache.kerberos_ccache_init(PRINCIPAL, KEYTAB_PATH,
                                              CCACHE_PATH if custom_ccache else None)


@pytest.mark.parametrize("custom_ccache", [True, False])
def test_kinit_recentcache(custom_ccache):
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    klist_out = yesterday.strftime(KLIST_TEMPLATE)

    flexmock(osbs.kerberos_ccache).should_receive('run') \
                                  .with_args(['klist'], extraenv=object) \
                                  .and_return(0, klist_out, "") \
                                  .once()
    flexmock(osbs.kerberos_ccache).should_receive('run') \
                                  .with_args(['kinit', '-k', '-t',
                                              KEYTAB_PATH, PRINCIPAL],
                                             extraenv=object) \
                                  .and_return(0, "", "") \
                                  .once()
    flexmock(os.environ).should_receive('__setitem__') \
                        .with_args("KRB5CCNAME", CCACHE_PATH) \
                        .times(1 if custom_ccache else 0)

    osbs.kerberos_ccache.kerberos_ccache_init(PRINCIPAL, KEYTAB_PATH,
                                              CCACHE_PATH if custom_ccache else None)


@pytest.mark.parametrize("custom_ccache", [True, False])
def test_kinit_newcache(custom_ccache):
    tomorrow = datetime.datetime.now() + datetime.timedelta(days=1)
    klist_out = tomorrow.strftime(KLIST_TEMPLATE)

    flexmock(osbs.kerberos_ccache).should_receive('run') \
                                  .with_args(['klist'], extraenv=object) \
                                  .and_return(0, klist_out, "") \
                                  .once()
    flexmock(osbs.kerberos_ccache).should_receive('run') \
                                  .with_args(['kinit', '-k', '-t',
                                              KEYTAB_PATH, PRINCIPAL],
                                             extraenv=object) \
                                  .never()
    flexmock(os.environ).should_receive('__setitem__') \
                        .with_args("KRB5CCNAME", CCACHE_PATH) \
                        .times(1 if custom_ccache else 0)

    osbs.kerberos_ccache.kerberos_ccache_init(PRINCIPAL, KEYTAB_PATH,
                                              CCACHE_PATH if custom_ccache else None)


@pytest.mark.parametrize("custom_ccache", [True, False])
def test_kinit_fails(custom_ccache):
    flexmock(osbs.kerberos_ccache).should_receive('run') \
                                  .with_args(['klist'], extraenv=object) \
                                  .and_return(1, "", "") \
                                  .once()
    flexmock(osbs.kerberos_ccache).should_receive('run') \
                                  .with_args(['kinit', '-k', '-t',
                                              KEYTAB_PATH, PRINCIPAL],
                                             extraenv=object) \
                                  .and_return(1, "error", "error") \
                                  .once()
    flexmock(os.environ).should_receive('__setitem__') \
                        .with_args("KRB5CCNAME", CCACHE_PATH) \
                        .never()

    with pytest.raises(OsbsException):
        osbs.kerberos_ccache.kerberos_ccache_init(PRINCIPAL, KEYTAB_PATH,
                                                  CCACHE_PATH if custom_ccache else None)


@pytest.mark.parametrize("prefix", ["", "some/thing"])
def test_tarfile(tmpdir, prefix):
    filename = str(tmpdir.join("archive.tar.bz2"))

    with TarWriter(filename, directory=prefix) as t:
        t.write_file("a/b.c", b"foobar")

    assert os.path.exists(filename)

    for f in TarReader(filename):
        assert f.filename == os.path.join(prefix, "a/b.c")
        content = f.fileobj.read()
        assert content == b"foobar"


def test_get_instance_token_file_name():
    expected = os.path.join(os.path.expanduser('~'), '.osbs', 'spam.token')

    assert get_instance_token_file_name('spam') == expected


vstr_re = re.compile(r'\d+\.\d+\.\d+')


@pytest.mark.parametrize(('version', 'valid'), [
    ('1.0.4', True),
    ('5.3', True),
    ('2', True),
    ('7.3.1', True),
    ('9', True),
    ('6.8', True),
    ('10.127.43', True),
    ('bacon', False),
    ('5x3yj', False),
    ('x.y.z', False),
])
def test_sanitize_version(version, valid):
    if valid:
        assert vstr_re.match(sanitize_version(parse_version(version)))
    else:
        # with old-style parse_version(), we'll get a numerical string
        # back from sanitize_version(), but current parse_version() will
        # give us a ValueError exception
        try:
            val = sanitize_version(parse_version(version))
            if val != version:
                return
        except BaseException:
            pass
        with pytest.raises(ValueError):
            sanitize_version(parse_version(version))


@pytest.mark.parametrize(('img', 'expected'), [
    ('fedora23', 'fedora23'),
    ('fedora23:sometag', 'fedora23'),
    ('fedora23/python', 'fedora23/python'),
    ('fedora23/python:sometag', 'fedora23/python'),
    ('docker.io/fedora23', 'fedora23'),
    ('docker.io/fedora23/python', 'fedora23/python'),
    ('docker.io/fedora23/python:sometag', 'fedora23/python'),
])
def test_strip_registry_and_tag_from_image(img, expected):
    assert strip_registry_and_tag_from_image(img) == expected


@pytest.mark.parametrize(('commit', 'branch', 'depth'), [
    (None, None, None),
    (TEST_DOCKERFILE_SHA1, None, None),
    (TEST_DOCKERFILE_SHA1, TEST_DOCKERFILE_BRANCH, 1),
    (TEST_DOCKERFILE_INIT_SHA1, TEST_DOCKERFILE_BRANCH, 1),
    (TEST_DOCKERFILE_SHA1, None, 1),
])
@requires_internet
def test_clone_git_repo(tmpdir, commit, branch, depth):
    tmpdir_path = str(tmpdir.realpath())
    repo_data = clone_git_repo(TEST_DOCKERFILE_GIT, tmpdir_path, commit=commit,
                               branch=branch, depth=depth)
    assert repo_data.commit_id is not None
    assert tmpdir_path == repo_data.repo_path
    if commit:
        assert commit == repo_data.commit_id
    assert len(repo_data.commit_id) == 40  # current git hashes are this long
    assert os.path.isdir(os.path.join(tmpdir_path, '.git'))


@requires_internet
def test_calc_depth_git_repo(tmpdir):
    tmpdir_path = str(tmpdir.realpath())
    repo_data = clone_git_repo(TEST_DOCKERFILE_GIT, tmpdir_path, commit='HEAD',
                               branch='master', depth=None)
    assert repo_data.commit_id is not None
    assert tmpdir_path == repo_data.repo_path
    assert repo_data.commit_depth == 1


@pytest.mark.parametrize(('commit', 'branch', 'depth'), [
    ("bad", None, None),
    ("bad", TEST_DOCKERFILE_BRANCH, 1),
    ("bad", TEST_DOCKERFILE_BRANCH, 1),
    ("bad", None, 1),
])
@requires_internet
def test_clone_git_repo_commit_failure(tmpdir, commit, branch, depth):
    tmpdir_path = str(tmpdir.realpath())
    with pytest.raises(OsbsCommitNotFound) as exc:
        clone_git_repo(TEST_DOCKERFILE_GIT, tmpdir_path, commit=commit, retry_times=1,
                       branch=branch, depth=depth)
    assert 'Commit {} is not reachable in branch {}'.format(commit, branch) in str(exc)


def test_clone_git_repo_total_failure(tmpdir):
    tmpdir_path = str(tmpdir.realpath())
    with pytest.raises(OsbsException) as exc:
        clone_git_repo(tmpdir_path + 'failure', tmpdir_path, retry_times=1)
    assert 'Unable to clone git repo' in exc.value.message


def test_get_repo_info(tmpdir):
    repo_path = tmpdir.mkdir("repo").strpath
    with open(os.path.join(repo_path, REPO_CONTAINER_CONFIG), 'w') as f:
        f.write(dedent("""\
            compose:
                modules:
                - n:s:v
            """))

    initialize_git_repo(repo_path, files=[REPO_CONTAINER_CONFIG])
    info = get_repo_info(repo_path, 'HEAD')
    assert isinstance(info, RepoInfo)
    assert info.configuration.container == {'compose': {'modules': ['n:s:v']}}


def initialize_git_repo(rpath, files=None):
    subprocess.Popen(['git', 'init', rpath])
    subprocess.Popen(['git', 'config', 'user.name', '"Gerald Host"'], cwd=rpath)
    subprocess.Popen(['git', 'config', 'user.email', '"ghost@example.com"'], cwd=rpath)
    first_commit_ref = None
    for f in files or []:
        subprocess.Popen(['touch', f], cwd=rpath)
        subprocess.Popen(['git', 'add', f], cwd=rpath)
        subprocess.Popen(['git', 'commit', '-m', 'new file {0}'.format(f)], cwd=rpath)
        if not first_commit_ref:
            first_commit_ref = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=rpath)
            first_commit_ref = first_commit_ref.strip()
    subprocess.Popen(['git', 'commit', '--allow-empty', '-m', 'code additions'], cwd=rpath)
    return first_commit_ref


def test_image_name_parse():
    for inp, parsed in TEST_DATA.items():
        assert ImageName.parse(inp) == parsed


def test_image_name_format():
    for expected, image_name in TEST_DATA.items():
        assert image_name.to_str() == expected


def test_image_name_parse_image_name(caplog):
    warning = 'Attempting to parse ImageName test:latest as an ImageName'
    test = ImageName.parse("test")
    assert warning not in caplog.text
    image_test = ImageName.parse(test)
    assert warning in caplog.text
    assert test is image_test


@pytest.mark.parametrize(('repo', 'organization', 'enclosed_repo'), (
    ('fedora', 'spam', 'spam/fedora'),
    ('spam/fedora', 'spam', 'spam/fedora'),
    ('spam/fedora', 'maps', 'maps/spam-fedora'),
))
@pytest.mark.parametrize('registry', (
    'example.registry.com',
    'example.registry.com:8888',
    None,
))
@pytest.mark.parametrize('tag', ('bacon', None))
def test_image_name_enclose(repo, organization, enclosed_repo, registry, tag):
    reference = repo
    if tag:
        reference = '{}:{}'.format(repo, tag)
    if registry:
        reference = '{}/{}'.format(registry, reference)

    image_name = ImageName.parse(reference)
    assert image_name.get_repo() == repo
    assert image_name.registry == registry
    assert image_name.tag == (tag or 'latest')

    image_name.enclose(organization)
    assert image_name.get_repo() == enclosed_repo
    # Verify that registry and tag are unaffected
    assert image_name.registry == registry
    assert image_name.tag == (tag or 'latest')


def test_image_name_comparison():
    # make sure that both "==" and "!=" are implemented right on both Python major releases
    i1 = ImageName(registry='foo.com', namespace='spam', repo='bar', tag='1')
    i2 = ImageName(registry='foo.com', namespace='spam', repo='bar', tag='1')
    assert i1 == i2
    assert not i1 != i2

    i2 = ImageName(registry='foo.com', namespace='spam', repo='bar', tag='2')
    assert not i1 == i2
    assert i1 != i2
