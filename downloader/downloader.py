#!/usr/bin/env python3
'''
downloader: download copies of repositories to local file system

This module contacts the CASICS server to get information about one or more
specified repositories, then downloads a copy of those repositories to the
local file system.
'''

from   base64 import b64encode
from   datetime import datetime
import errno
import github3
from   halo import Halo
import http
import humanize
import locale
import magic
import os
import plac
from   pymongo import MongoClient
import requests
import shutil
import sys
import urllib
from   time import time, sleep
import wget
import zipfile

sys.path.append('..')

from common.casics import *
from common.messages import *
from common.credentials import *


# Global constants.
# .............................................................................

_CONN_TIMEOUT = 5000
'''Time to wait for connection to databases, in milliseconds.'''

_MAX_FAILURES = 10
'''Total number of failures allowed before we pause.'''

_MAX_RETRIES  = 3
'''Number of times we pause & retry after hitting the failure limit.'''

# GitHub defaults.

_GITHUB_KEYRING = 'org.casics.github'

# CASICS database defaults.

_CASICS_DEFAULT_HOST = 'localhost'
'''Default network host for CASICS server if no explicit host is given.'''

_CASICS_DEFAULT_PORT = 27017
'''Default network port for CASICS server if no explicit port number is given.'''

_CASICS_DB_NAME = 'github'
'''The name of the CASICS database.'''

_CASICS_KEYRING = "org.casics.casics"
'''The name of the keyring entry for LoCTerms client users.'''


# Main interface.
# .............................................................................
# Currently this only does GitHub, but extending this to handle other hosts
# should hopefully be possible.

@plac.annotations(
    file        = ('file containing repository identifiers',            'option', 'f'),
    id          = ('comma-separated list of repo ids on command line',  'option', 'i'),
    root        = ('root of directory where downloads will be written', 'option', 'r'),
    casics_user = ('CASICS database user name',                         'option', 'u'),
    casics_pswd = ('CASICS database user password',                     'option', 'p'),
    casics_host = ('CASICS database server host',                       'option', 's'),
    casics_port = ('CASICS database connection port number',            'option', 'o'),
    github_user = ('GitHub database user name',                         'option', 'U'),
    github_pswd = ('GitHub database user password',                     'option', 'P'),
    nokeyring   = ('do not use a keyring',                              'flag',   'X'),
    nofrills    = ('do not show progress spinners or color output',     'flag',   'Y'),
)

def main(root=None, file=None, id=None, nokeyring=False, nofrills=False,
         casics_user=None, casics_pswd=None, casics_host=None, casics_port=None,
         github_user=None, github_pswd=None):
    '''Download copies of repositories to local file system.

This module contacts the CASICS server to get information about one or more
specified repositories, then downloads a copy of those repositories to the
local file system.  The option `-r` is required, and one of the options `-f`
or `-i` must also be given.  Basic usage:

    downloader -r /path/to/downloads/root -f file-of-repo-identifiers.txt
or
    downloader -r /path/to/downloads/root -i ID,ID,ID,...

If a file of repository identifiers is given with `-f`, the file must have one
repository numeric identifier per line.  If a list of identifiers is given
with `-i` on the command line, it should be one or more numeric identifiers
separated by commas with no spaces between them.

By default, this uses the operating system's keyring/keychain functionality
to get the user name and password needed to access both the CASICS database
server and GitHub over the network.  If no such credentials are found, it
will query the user interactively for the user name and password for each
system separately (so, two sets), and then store them in the keyring/keychain
(unless the -X argument is given) so that it does not have to ask again in
the future.  It is also possible to supply user names and passwords directly
using command line arguments, but this is discouraged because it is insecure
on multiuser computer systems. (Other users could run "ps" in the background
and see your credentials).

Additional arguments can be used to specify the host and port on which the
CASICS database.

The output will use spinners and color, unless the no-frills option `-Y` is
given.'''
    # Dealing with negated variables is confusing, so turn them around.
    keyring = not nokeyring
    showprogress = not nofrills
    colorize = 'termcolor' in sys.modules and not nofrills

    # Check arguments.
    if not root:
        raise SystemExit(colorcode('Need root of directory where downloads should go.',
                                   'error', colorize))
    elif not os.path.exists(root):
        raise SystemExit(colorcode('"{}" does not exist.'.format(root),
                                   'error', colorize))
    elif not os.path.isdir(root):
        raise SystemExit(colorcode('"{}" is not a directory.'.format(root),
                                   'error', colorize))

    if id:
        id_list = [int(x) for x in id.split(',')]
    elif file:
        with open(file) as f:
            id_list = [int(x) for x in f.read().splitlines()]
    else:
        raise SystemExit(colorcode('Need identifiers of repositories to be downloaded.',
                                   'error', colorize))

    if not (casics_user and casics_pswd and casics_host and casics_port):
        (casics_user, casics_pswd, casics_host, casics_port) = obtain_credentials(
            _CASICS_KEYRING, "CASICS", casics_user, casics_pswd, casics_host,
            casics_port, _CASICS_DEFAULT_HOST, _CASICS_DEFAULT_PORT)
    if not (github_user and github_pswd):
        (github_user, github_pswd, _, _) = obtain_credentials(
            _GITHUB_KEYRING, "GitHub", github_user, github_pswd, -1, -1)
    if keyring:
        # Save the credentials if they're different from what's saved.
        (s_user, s_pswd, s_host, s_port) = get_keyring_credentials(_CASICS_KEYRING)
        if s_user != casics_user or s_pswd != casics_pswd or \
           s_host != casics_host or s_port != casics_port:
            save_keyring_credentials(_CASICS_KEYRING, casics_user, casics_pswd,
                                     casics_host, casics_port)
        (s_user, s_pswd, _, _) = get_keyring_credentials(_GITHUB_KEYRING)
        if s_user != github_user or s_pswd != github_pswd:
            save_keyring_credentials(_GITHUB_KEYRING, github_user, github_pswd)

    # Set locale so that file names end up with appropriate character encodings
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    # And we're ready to get to it.
    repos = get_repos(casics_user, casics_pswd, casics_host, casics_port)
    get_sources(repos, root, id_list, github_user, github_pswd, showprogress, colorize)


def get_sources(repos, downloads_root, id_list, user, password, showprogress, colorize):
    downloads_tmp = os.path.join(downloads_root, 'tmp')
    os.makedirs(downloads_tmp, exist_ok=True)

    msg('Starting {}.'.format(datetime.now().strftime("%Y-%m-%d %H:%M")), 'info', colorize)
    msg('Downloading {} repos to {}'.format(len(id_list), downloads_root), 'info', colorize)
    msg('Using temporary directory in {}'.format(downloads_tmp), 'info', colorize)

    fields = dict.fromkeys(['owner', 'name', 'default_branch'], 1)

    count = 0
    failures = 0
    retries = 0
    start = time()
    for id in iter(id_list):
        retry = True
        while retry and failures < _MAX_FAILURES:
            # Don't retry unless the problem may be transient.
            retry = False
            entry = repos.find_one({'_id': id}, fields)
            if not entry:
                msg('*** skipping unknown GitHub id {}'.format(id), 'warning', colorize)
                continue
            if showprogress:
                spinner = Halo(text=colorcode('{} '.format(e_summary(entry)),
                                              'info', colorize), spinner='boxBounce')
                spinner.start()
            else:
                msg('{} '.format(e_summary(entry)), 'info', colorize)
            if download(entry, downloads_tmp, downloads_root, user, password, colorize):
                failures = 0
            else:
                failures += 1
            if showprogress:
                spinner.stop()

        if failures >= _MAX_FAILURES:
            # Try pause & continue in case of transient network issues.
            if retries <= _MAX_RETRIES:
                retries += 1
                msg('')
                msg('*** Pausing because of too many consecutive failures',
                    'warning', colorize)
                sleep(300 * retries)
                failures = 0
            else:
                # We've already paused & restarted.
                msg('')
                msg('*** Stopping because of too many consecutive failures',
                    'error', colorize)
                break
        count += 1
        if count % 100 == 0:
            msg('{} [{:2f}]'.format(count, time() - start))
            start = time()
    msg('')
    msg('Done {}.'.format(datetime.now().strftime("%Y-%m-%d %H:%M")), 'info', colorize)


def download(entry, downloads_tmp, downloads_root, user, password, colorize):
    localpath = generate_path(downloads_root, entry['_id'])
    if os.path.exists(localpath) and os.listdir(localpath):
        # Skip it if we already have it.
        msg('already in {} -- skipping'.format(localpath), 'blue', colorize)
        return True

    # Try first with the default master branch.
    outfile = None
    start = datetime.now()
    try:
        url = "https://github.com/{}/{}/archive/{}.zip".format(
            entry['owner'], entry['name'], entry['default_branch'])
        outfile = wget.download(url, bar=None, out=downloads_tmp)
    except Exception as e:
        # If we get a 404 from GitHub, it may mean there is no zip file for
        # what we think is the default branch.  To find out what it really
        # is, we first try scraping the web page, and if that fails, we
        # resort to using an API call.
        if hasattr(e, 'code') and e.code == 404:
            msg('no zip file for branch {} -- looking at alternatives'
                .format(entry['default_branch']), 'warning', colorize)
            newurl = get_archive_url_by_scraping(entry)
            if newurl:
                try:
                    outfile = wget.download(newurl, bar=None, out=downloads_tmp)
                except Exception as newe:
                    msg('*** failed to download: {}'.format(str(newe)),
                        'warning', colorize)
                    return False
            else:
                newurl = get_archive_url_by_api(entry, user, password)
                if newurl:
                    try:
                        outfile = wget.download(newurl, bar=None, out=downloads_tmp)
                    except Exception as newe:
                        msg('*** failed to download: {}'.format(str(newe)),
                            'error', colorize)
                        return False
                else:
                    msg("*** can't find download URL for branch '{}'".format(
                        entry['default_branch']), 'error', colorize)
                    return False
        else:
            msg('*** failed to download: {}'.format(str(e)))
            return False

    # Unzip it to a temporary path, then move it to the final location.

    filesize = file_size(outfile)
    try:
        outdir = unzip_archive(outfile, downloads_tmp)
        os.remove(outfile)
    except Exception as e:
        msg('{} left zipped: {}'.format(outfile, str(e)), 'info', colorize)
        return False

    os.makedirs(os.path.dirname(localpath), exist_ok=True)
    os.rename(outdir, localpath)

    td = str(datetime.now() - start).split('.')[0]
    msg('--> {}, {}, time {}'.format(localpath, filesize, td), 'info', colorize)
    return True


# Helpers.
# .............................................................................

def get_repos(user, password, host, port):
    db = MongoClient('mongodb://{}:{}@{}:{}/github?authSource=admin'
                     .format(user, password, host, port),
                     serverSelectionTimeoutMS=_CONN_TIMEOUT,
                     tz_aware=True, connect=True, socketKeepAlive=True)
    github_db = db[_CASICS_DB_NAME]
    repos_collection = github_db.repos
    return repos_collection


def unzip_archive(file, dest):
    # This only unzips what it guesses to be text files; for binary files,
    # it only creates the file name but leaves out the content.  (This is
    # our approach to saving disk space, because our analysis approach needs
    # text only.)  It returns the path to the directory where of the contents.

    zf = zipfile.ZipFile(file, 'r')
    infolist = zf.infolist()
    # The first item is the name of the root directory.
    if infolist:
        outdir = os.path.join(os.path.dirname(file), infolist[0].filename)
    for component in infolist:
        # zip files often cause problems on Linux because Linux file/dir
        # names don't allow many character encodings.  I thought that setting
        # the locale (see earlier in this file) would take care of that, but
        # it didn't.  That's the reason for the encode/decode wonkiness here.
        name = component.filename.encode('utf-8', 'ignore').decode('ascii', 'ignore')
        dir_name = os.path.join(dest, os.path.dirname(name))
        full_path = os.path.join(dir_name, os.path.basename(name))

        # Create missing directories.
        os.makedirs(dir_name, exist_ok=True)

        # Write files.
        try:
            if not name.endswith('/'):
                content = zf.read(component)
                if probably_text(name, content):
                    with open(full_path, 'wb') as fd:
                        fd.write(content)
                else:
                    os.mknod(full_path)
        except:
            continue

    zf.close()
    return outdir


def probably_text(filename, content):
    if not content:
        # Empty files are considered text.
        return True
    return (str(magic.from_buffer(content)).find('text') >= 0)


def file_size(path):
    return humanize.naturalsize(os.path.getsize(path))


def get_home_page_text(entry):
    url = 'http://github.com/' + entry['owner'] + '/' + entry['name']
    r = requests.get(url)
    return r.text if r.status_code == 200 else None


def get_archive_url_by_scraping(entry):
    html = get_home_page_text(entry)
    if not html:
        return None
    regionstart = html.find('<div class="file-navigation-option">')
    if regionstart < 1:
        return None
    regionend = html.find('Download ZIP', regionstart)
    pathstart = html.find('<a href="', regionstart, regionend)
    if not pathstart:
        return None
    pathstartlen = len('<a href="')
    pathend = html.find('"', pathstart + pathstartlen)
    return 'http://github.com/' + html[pathstart + pathstartlen + 1 : pathend]


def get_archive_url_by_api(entry, user, password):
    auth = '{0}:{1}'.format(user, password)
    headers = {
        'User-Agent': user,
        'Authorization': 'Basic ' + b64encode(bytes(auth, 'ascii')).decode('ascii'),
        'Accept': 'application/vnd.github.v3.raw',
    }
    url = "https://api.github.com/repos/{}/{}/zipball".format(
        entry['owner'], entry['name'])

    conn = http.client.HTTPSConnection("api.github.com")
    conn.request("GET", url, {}, headers)
    response = conn.getresponse()
    if response.status == 302:
        try:
            return next(y for x, y in response.getheaders() if x == 'Location')
        except:
            return None
    elif response.status == 200:
        return response.readall().decode('utf-8')
    else:
        return None


# For Emacs users
# ......................................................................
# Local Variables:
# mode: python
# python-indent-offset: 4
# End:
