#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2018 Andrea Chiavazza
# Licensed under the MIT License
# See https://opensource.org/licenses/mit-license.php

# Requirements: gpg
# Works only on Linux

import argparse, re, sys, platform, os, signal, subprocess, \
       shlex, hashlib, shutil, tarfile, time, json
from urllib.request import urlopen, Request, URLError
from collections import namedtuple

HOME_PATH        = os.path.expanduser('~') + '/'
TARS_PATH        = HOME_PATH + 'tars/'
BIN_PATH         = HOME_PATH + 'bin/'
MAN_PATH         = HOME_PATH + '.local/share/man/man1/'
LINK_NAME        = 'current'
RETRY_SECS       = 0.3
START_SECS       = 2
COLUMN_WIDTH     = 14
ACTIONS_WIDTH    = 56
FAILED           = 'Failed'

BTC_INSTALL_PATH = HOME_PATH + 'opt/bitcoin/'
BTC_DAEMON_BIN   = 'bitcoind'
BTC_CLIENT_BIN   = 'bitcoin-cli'
BTC_TEST_BIN     = 'test_bitcoin'
BTC_ROOT_URL     = 'https://bitcoin.org/bin/'
BTC_WEB_PREFIX   = 'bitcoin-core-'

LND_INSTALL_PATH = HOME_PATH + 'opt/lnd/'
LND_DAEMON_BIN   = 'lnd'
LND_CLIENT_BIN   = 'lncli'
LND_API_URL      = 'https://api.github.com/repos/lightningnetwork/lnd/releases/latest'

Daemon = namedtuple('Daemon', 'keyId keyUrl, checksumFilePat, remoteUrlPat, tarPattern')

BTC = Daemon(
    keyId = '01EA5486DE18A882D4C2684590C8019E36C2E964',
    keyUrl = 'https://bitcoin.org/laanwj-releases.asc',
    checksumFilePat = 'SHA256SUMS.asc',
    remoteUrlPat = 'https://bitcoin.org/bin/bitcoin-core-{0}/',
    tarPattern = 'bitcoin-{0}-{1}.tar.gz')

LND = Daemon(
    keyId = 'F8037E70C12C7A263C032508CE58F7F8E20FD9A2',
    keyUrl = 'https://keybase.io/roasbeef/pgp_keys.asc',
    checksumFilePat = 'manifest-{}.txt',
    remoteUrlPat = 'https://github.com/lightningnetwork/lnd/releases/download/{0}/',
    tarPattern = 'lnd-linux-{1}-{0}.tar.gz')

def sh(s, out=subprocess.PIPE):
    return subprocess.run(shlex.split(s), stdout=out, stderr=subprocess.PIPE)

def shBG(s):
    return subprocess.Popen(shlex.split(s), stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT).pid

def getComm(pid):
    try:
        with open(os.path.join('/proc', str(pid), 'comm'), 'r') as filein:
            return filein.read().strip()
    except FileNotFoundError:
        return ''

def getPids(comm):
    pids = map(int, filter(str.isdigit, os.listdir('/proc')))
    return set(filter(lambda pid: getComm(pid) == comm, pids))

def log(desc, value):
    if desc != '':
        print((desc + ': ').ljust(ACTIONS_WIDTH), end='', flush=True)
    if isinstance(value, str):
        if value != '':
            print(value)
    elif value:
        print('OK')
    else:
        sys.exit('##### FAILED #####')

def getRemoteFileSize(url):
    try:
        with urlopen(Request(url, method='HEAD')) as netin:
            return int(netin.getheader('Content-Length'))
    except URLError:
        return -1

def saveRemoteFile(url, localPath, resume):
    try:
        log('Retrieving ' + os.path.basename(url), '')
        request = Request(url)
        if resume and os.path.isfile(localPath):
            currentSize = os.stat(localPath).st_size
            remoteFileSize = getRemoteFileSize(url)
            if currentSize < remoteFileSize:
                request.add_header('Range', 'bytes={}-'.format(currentSize))
            elif currentSize == remoteFileSize:
                log('', True)
                return
        with urlopen(request) as response, \
             open(localPath, 'ab' if resume else 'wb') as outfile:
            shutil.copyfileobj(response, outfile)
        log('', True)
    except URLError:
        log('', False)

def getGccArch():
    machine = platform.machine() # same as 'uname -m'
    if 'arm' in machine:
        return 'arm-linux-gnueabihf'
    elif machine == 'i686':
        return 'i686-pc-linux-gnu'
    else: # Works for x86_64 and aarch64. Best guess for unknown.
        return machine + '-linux-gnu'

def getGoArch():
    machine = platform.machine() # same as 'uname -m'
    if machine == 'i686':
        return '386'
    elif machine == 'x86_64':
        return 'amd64'
    elif machine == 'aarch64':
        return 'arm64'
    elif machine[:3] == 'arm':
        return 'arm'
    sys.exit('##### Unknown architecture ' + machine)

def stopDaemon(daemon, clientPath):
    if sh(clientPath + ' stop').returncode != 0:
        for pid in getPids(daemon):
            os.kill(pid, signal.SIGTERM)
    while getPids(daemon):
        time.sleep(RETRY_SECS)

def restartBtc():
    log('Restarting ' + BTC_DAEMON_BIN, '')
    stopDaemon(BTC_DAEMON_BIN, BIN_PATH + BTC_CLIENT_BIN)
    p = sh(BIN_PATH + BTC_DAEMON_BIN + ' -daemon')
    log('', True if p.returncode == 0 else FAILED)

def restartLnd():
    log('Restarting ' + LND_DAEMON_BIN, '')
    stopDaemon(LND_DAEMON_BIN, BIN_PATH + LND_CLIENT_BIN)
    pid = shBG(BIN_PATH + LND_DAEMON_BIN)
    log('', FAILED if getComm(pid) == '' else True)

def makeLink(src, dest):
    src = os.path.normpath(src)
    dest = os.path.normpath(dest)
    if (not os.path.islink(dest)) or os.readlink(dest) != src:
        if os.path.lexists(dest):
            os.remove(dest)
        os.symlink(src, dest)

def getInstalledVersion(path, rexp):
    try:
        p = sh(path + ' --version')
        if p.returncode != 0:
            sys.exit('##### Error getting version of command ' + path)
        m = re.match(rexp, p.stdout.decode())
        if m is None:
            sys.exit('##### Error parsing output of command ' + path)
        return m.group(1)
    except IndexError:
        sys.exit('##### Error parsing output of command ' + path)
    except FileNotFoundError:
        return None

def getJson(cmd, rexp, field):
    p = sh(cmd)
    if p.returncode != 0:
        return None
    m = re.match(rexp, json.loads(p.stdout.decode())[field])
    if m is None:
        sys.exit('##### Error parsing json value returned by ' + cmd)
    return m.group(1)

def getRunningBtc():
    while getPids(BTC_DAEMON_BIN):
        ver = getJson(BIN_PATH + BTC_CLIENT_BIN + ' getnetworkinfo',
                      r'^/Satoshi:(.*)/', 'subversion')
        if ver is not None:
            return ver
        time.sleep(RETRY_SECS)

def getRunningLnd():
    # "lncli getinfo" returns 1 if either lnd is not running or if wallet is locked
    # if lnd is not running stderr contains 'the connection in unavailable'
    # if lnd is locked      stderr contains 'Wallet is encrypted'
    ver = getJson(BIN_PATH + LND_CLIENT_BIN + ' getinfo', r'^(\S*)', 'version')
    if ver:
        return ver
    elif getPids(LND_DAEMON_BIN):
        return 'locked'
    else:
        return None

def getExpectedChecksum(checksumFilePath, fileName):
    try:
        with open(checksumFilePath, 'r') as filein:
            lines = filein.read().splitlines()
            try:
                begin = lines.index('-----BEGIN PGP SIGNED MESSAGE-----')
                end   = lines.index('-----BEGIN PGP SIGNATURE-----')
                lines = lines[(begin + 1):end]
            except ValueError:
                pass
            p = re.compile(r'^([0-9a-fA-F]{64})[ ]+' + re.escape(fileName) + '$')
            hashes = [m.group(1) for m in map(p.match, lines)
                                 if m is not None]
            if len(hashes) > 0 and all(x == hashes[0] for x in hashes[1:]):
                return hashes[0]
            else:
                return None
    except IndexError:
        return None

def installTar(tarPath, instDir):
    try:
        # Delete existing files to avoid errors for open/running files
        if os.path.isdir(instDir):
            shutil.rmtree(instDir)
        elif os.path.lexists(instDir):
            os.remove(instDir)
        os.makedirs(instDir, exist_ok=False)
        with tarfile.open(tarPath, errorlevel=2) as tar:
            for member in tar.getmembers():
                if os.path.dirname(member.name) != '':
                    member.name = '/'.join(member.name.split('/')[1:])
                    tar.extract(member, instDir)
            return True
    except (OSError, tarfile.ExtractError):
        return False

def runGPG(args):
    return sh('gpg --batch --logger-fd 1 --trust-model always ' + args)

def haveKey(key):
    return runGPG('-k ' + key).returncode == 0

def retrievePublicKey(keyId, keyUrl):
    if haveKey(keyId):
        return
    if keyUrl is not None:
        log('Importing key ' + keyUrl, '')
        runGPG('--fetch-keys ' + keyUrl)
        if haveKey(keyId):
            log('', True)
            return
        log('', FAILED)
    log('Importing key from gpg server', '')
    runGPG('--recv-keys ' + keyId)
    log('', haveKey(keyId))

def verifyGPG(sigPath, dataPath):
    data = '' if dataPath is None or sigPath == dataPath else dataPath
    return runGPG('--verify {} {}'.format(sigPath, data)).returncode == 0

def checkSha256(filePath, sha256sum):
    try:
        with open(filePath, 'rb') as filein:
            return hashlib.sha256(filein.read()).hexdigest().lower() == sha256sum.lower()
    except OSError:
        return False

def verifyChecksum(checksum, filePath, url):
    if checkSha256(filePath, checksum):
        return True
    else:
        saveRemoteFile(url, filePath, True)
        return checkSha256(filePath, checksum)

def getValidatedArchive(version, arch, data):
    checksumFile = data.checksumFilePat.format(version)
    (f, e) = os.path.splitext(checksumFile)
    if f.endswith('-' + version):
        localChecksumFile = checksumFile
    else:
        localChecksumFile = f + '-' + version + e
    sigData = TARS_PATH + localChecksumFile
    rootUrl = data.remoteUrlPat.format(version)
    postfix = '.sig' if e != '.asc' else ''
    sig = sigData + postfix
    if not verifyGPG(sig, sigData):
        retrievePublicKey(data.keyId, data.keyUrl)
        saveRemoteFile(rootUrl + checksumFile + postfix, sig, False)
        if postfix:
            saveRemoteFile(rootUrl + checksumFile, sigData, False)
        if not verifyGPG(sig, sigData):
            return None
    fileName = data.tarPattern.format(version, arch)
    checksum = getExpectedChecksum(sigData, fileName)
    if verifyChecksum(checksum, TARS_PATH + fileName, rootUrl + fileName):
        return TARS_PATH + fileName
    return None

def createLndLinks():
    root = LND_INSTALL_PATH + LINK_NAME
    os.makedirs(BIN_PATH, exist_ok=True)
    for prog in os.listdir(root):
        makeLink(root + '/' + prog, BIN_PATH + prog)
    log('Lnd symbolic links created', True)

def createBtcLinks(gccArch):
    root = BTC_INSTALL_PATH + gccArch + '/' + LINK_NAME
    os.makedirs(BIN_PATH, exist_ok=True)
    for prog in os.listdir(root + '/bin'):
        makeLink(root + '/bin/' + prog, BIN_PATH + prog)
    os.makedirs(MAN_PATH, exist_ok=True)
    for man in os.listdir(root + '/share/man/man1/'):
        makeLink(root + '/share/man/man1/' + man, MAN_PATH + man)
    log('Bitcoin symbolic links created', True)

def getLatestBtc():
    try:
        with urlopen(BTC_ROOT_URL) as netin:
            exists = lambda ver: getRemoteFileSize(BTC_ROOT_URL + BTC_WEB_PREFIX +
                                                   ver + '/' + BTC.checksumFilePat) > 0
            versions = re.findall('<a href="{}([0-9.]*)/">.*</a>'.format(BTC_WEB_PREFIX),
                                  netin.read().decode())
            sortedVersions = sorted(versions,
                                    key=lambda ver: [int(x) for x in ver.split('.')],
                                    reverse = True)
            return next(filter(exists, sortedVersions), None)
    except URLError:
        sys.exit('##### Error retrieving ' + BTC_ROOT_URL)

def getInstalledBtcClientVersion(path):
    return getInstalledVersion(path + 'bin/' + BTC_CLIENT_BIN,
                               r'^Bitcoin Core RPC client version v(.*)\n')

def getInstalledBtcDaemonVersion(path):
    return getInstalledVersion(path + 'bin/' + BTC_DAEMON_BIN,
                               r'^Bitcoin Core Daemon version v(.*)\n')

def updateBtc(restart, runTests):
    arch = getGccArch()
    installLink = BTC_INSTALL_PATH + arch + '/' + LINK_NAME + '/'
    installedVer = getInstalledBtcDaemonVersion(installLink)
    latestVer = getLatestBtc()
    if installedVer != latestVer:
        print('Upgrading bitcoind from {} to {}'.format(installedVer, latestVer))
        fileName = getValidatedArchive(latestVer, arch, BTC)
        log('Signature of ' + latestVer, fileName is not None)
        log('Extracting archive', '')
        dirName = 'bitcoin-' + latestVer
        instDir = BTC_INSTALL_PATH + arch + '/' + dirName + '/'
        log('', installTar(fileName, instDir))
        testBinPath = instDir + 'bin/' + BTC_TEST_BIN
        if runTests and os.path.isfile(testBinPath):
            p = sh(testBinPath, None)
            print(p.stderr.decode().strip())
            if p.returncode != 0:
                sys.exit(p.returncode)
        if latestVer == getInstalledBtcDaemonVersion(instDir) == \
                        getInstalledBtcClientVersion(instDir):
            os.sync() # make sure all newly installed files are synced before switching
            makeLink(dirName, installLink)
            createBtcLinks(arch)
            installedVer = latestVer
        else:
            sys.exit('##### Downloaded binaries don\'t have the expected version.')
    if restart and getRunningBtc() != installedVer:
        restartBtc()
    return (BTC_DAEMON_BIN, latestVer, installedVer, getRunningBtc())

def lndTagToVersion(tag):
    m = re.match(r'v([0-9\.]*)-(.*)', tag)
    if m is None:
        sys.exit('##### Error parsing lnd tag \'{}\''.format(tag))
    ver = m.group(1)
    return ver + ('-' if ver.count('.') > 1 else '.0-') + m.group(2)

def getLatestLndTag():
    try:
        with urlopen(LND_API_URL) as netin:
            return json.loads(netin.read().decode())['tag_name']
    except URLError:
        sys.exit('##### Error retrieving ' + LND_API_URL)

def getInstalledLndDaemonVersion(path):
    return getInstalledVersion(path + LND_DAEMON_BIN,
                               r'^lnd version (.*) .*\n')

def updateLnd(restart):
    installLink = LND_INSTALL_PATH + LINK_NAME + '/'
    latestTag = getLatestLndTag()
    installedVer = getInstalledLndDaemonVersion(installLink)
    latestVer = lndTagToVersion(latestTag)
    if installedVer != latestVer:
        print('Upgrading lnd from {} to {}'.format(installedVer, latestVer))
        arch = getGoArch()
        fileName = getValidatedArchive(latestTag, arch, LND)
        log('Signature of ' + latestVer, fileName is not None)
        log('Extracting archive', '')
        dirName = 'lnd-linux-' + arch + '-' + latestTag
        instDir = LND_INSTALL_PATH + dirName + '/'
        log('', installTar(fileName, instDir))
        if latestVer == getInstalledLndDaemonVersion(instDir):
            os.sync() # make sure all newly installed files are synced before switching
            makeLink(dirName, installLink)
            createLndLinks()
            installedVer = latestVer
    if restart and getRunningLnd() != installedVer:
        restartLnd()
    return (LND_DAEMON_BIN, latestVer, installedVer, getRunningLnd())

def main():
    parser = argparse.ArgumentParser('Installs the latest version of bitcoind/lnd.')
    group1 = parser.add_mutually_exclusive_group()
    group1.add_argument('-b', action='store_true', help='process only bitcoind')
    group1.add_argument('-l', action='store_true', help='process only lnd')
    parser.add_argument('-s', action='store_true', help='skip the tests')
    parser.add_argument('-r', action='store_true', help='restart the daemons if not latest')
    args = parser.parse_args()
    os.makedirs(TARS_PATH, exist_ok=True)
    table = [('', 'Latest', 'Installed', 'Running')]
    if not args.l:
        table.append(updateBtc(args.r, not args.s))
    if not args.b:
        table.append(updateLnd(args.r))
    for rowno in range(0, len(table[0])):
        print(''.join(str(cols[rowno]).ljust(COLUMN_WIDTH) for cols in table))

if __name__ == "__main__":
    main()
