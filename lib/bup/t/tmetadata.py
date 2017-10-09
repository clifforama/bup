
import errno, glob, grp, pwd, stat, tempfile, subprocess

from wvtest import *

from bup import git, metadata
from bup import vfs2 as vfs
from bup.helpers import clear_errors, detect_fakeroot, is_superuser, resolve_parent
from bup.repo import LocalRepo
from bup.xstat import utime, lutime
from buptest import no_lingering_errors, test_tempdir
import bup.helpers as helpers


top_dir = '../../..'
bup_tmp = os.path.realpath('../../../t/tmp')
bup_path = top_dir + '/bup'
start_dir = os.getcwd()


def ex(*cmd):
    try:
        cmd_str = ' '.join(cmd)
        print >> sys.stderr, cmd_str
        rc = subprocess.call(cmd)
        if rc < 0:
            print >> sys.stderr, 'terminated by signal', - rc
            sys.exit(1)
        elif rc > 0:
            print >> sys.stderr, 'returned exit status', rc
            sys.exit(1)
    except OSError as e:
        print >> sys.stderr, 'subprocess call failed:', e
        sys.exit(1)


def setup_testfs():
    assert(sys.platform.startswith('linux'))
    # Set up testfs with user_xattr, etc.
    if subprocess.call(['modprobe', 'loop']) != 0:
        return False
    subprocess.call(['umount', 'testfs'])
    ex('dd', 'if=/dev/zero', 'of=testfs.img', 'bs=1M', 'count=32')
    ex('mke2fs', '-F', '-j', '-m', '0', 'testfs.img')
    ex('rm', '-rf', 'testfs')
    os.mkdir('testfs')
    ex('mount', '-o', 'loop,acl,user_xattr', 'testfs.img', 'testfs')
    # Hide, so that tests can't create risks.
    os.chown('testfs', 0, 0)
    os.chmod('testfs', 0o700)
    return True


def cleanup_testfs():
    subprocess.call(['umount', 'testfs'])
    helpers.unlink('testfs.img')


@wvtest
def test_clean_up_archive_path():
    with no_lingering_errors():
        cleanup = metadata._clean_up_path_for_archive
        WVPASSEQ(cleanup('foo'), 'foo')
        WVPASSEQ(cleanup('/foo'), 'foo')
        WVPASSEQ(cleanup('///foo'), 'foo')
        WVPASSEQ(cleanup('/foo/bar'), 'foo/bar')
        WVPASSEQ(cleanup('foo/./bar'), 'foo/bar')
        WVPASSEQ(cleanup('/foo/./bar'), 'foo/bar')
        WVPASSEQ(cleanup('/foo/./bar/././baz'), 'foo/bar/baz')
        WVPASSEQ(cleanup('/foo/./bar///././baz'), 'foo/bar/baz')
        WVPASSEQ(cleanup('//./foo/./bar///././baz/.///'), 'foo/bar/baz/')
        WVPASSEQ(cleanup('./foo/./.bar'), 'foo/.bar')
        WVPASSEQ(cleanup('./foo/.'), 'foo')
        WVPASSEQ(cleanup('./foo/..'), '.')
        WVPASSEQ(cleanup('//./..//.../..//.'), '.')
        WVPASSEQ(cleanup('//./..//..././/.'), '...')
        WVPASSEQ(cleanup('/////.'), '.')
        WVPASSEQ(cleanup('/../'), '.')
        WVPASSEQ(cleanup(''), '.')


@wvtest
def test_risky_path():
    with no_lingering_errors():
        risky = metadata._risky_path
        WVPASS(risky('/foo'))
        WVPASS(risky('///foo'))
        WVPASS(risky('/../foo'))
        WVPASS(risky('../foo'))
        WVPASS(risky('foo/..'))
        WVPASS(risky('foo/../'))
        WVPASS(risky('foo/../bar'))
        WVFAIL(risky('foo'))
        WVFAIL(risky('foo/'))
        WVFAIL(risky('foo///'))
        WVFAIL(risky('./foo'))
        WVFAIL(risky('foo/.'))
        WVFAIL(risky('./foo/.'))
        WVFAIL(risky('foo/bar'))
        WVFAIL(risky('foo/./bar'))


@wvtest
def test_clean_up_extract_path():
    with no_lingering_errors():
        cleanup = metadata._clean_up_extract_path
        WVPASSEQ(cleanup('/foo'), 'foo')
        WVPASSEQ(cleanup('///foo'), 'foo')
        WVFAIL(cleanup('/../foo'))
        WVFAIL(cleanup('../foo'))
        WVFAIL(cleanup('foo/..'))
        WVFAIL(cleanup('foo/../'))
        WVFAIL(cleanup('foo/../bar'))
        WVPASSEQ(cleanup('foo'), 'foo')
        WVPASSEQ(cleanup('foo/'), 'foo/')
        WVPASSEQ(cleanup('foo///'), 'foo///')
        WVPASSEQ(cleanup('./foo'), './foo')
        WVPASSEQ(cleanup('foo/.'), 'foo/.')
        WVPASSEQ(cleanup('./foo/.'), './foo/.')
        WVPASSEQ(cleanup('foo/bar'), 'foo/bar')
        WVPASSEQ(cleanup('foo/./bar'), 'foo/./bar')
        WVPASSEQ(cleanup('/'), '.')
        WVPASSEQ(cleanup('./'), './')
        WVPASSEQ(cleanup('///foo/bar'), 'foo/bar')
        WVPASSEQ(cleanup('///foo/bar'), 'foo/bar')


@wvtest
def test_metadata_method():
    with no_lingering_errors():
        with test_tempdir('bup-tmetadata-') as tmpdir:
            bup_dir = tmpdir + '/bup'
            data_path = tmpdir + '/foo'
            os.mkdir(data_path)
            ex('touch', data_path + '/file')
            ex('ln', '-s', 'file', data_path + '/symlink')
            test_time1 = 13 * 1000000000
            test_time2 = 42 * 1000000000
            utime(data_path + '/file', (0, test_time1))
            lutime(data_path + '/symlink', (0, 0))
            utime(data_path, (0, test_time2))
            ex(bup_path, '-d', bup_dir, 'init')
            ex(bup_path, '-d', bup_dir, 'index', '-v', data_path)
            ex(bup_path, '-d', bup_dir, 'save', '-tvvn', 'test', data_path)
            git.check_repo_or_die(bup_dir)
            repo = LocalRepo()
            resolved = vfs.lresolve(repo,
                                    '/test/latest' + resolve_parent(data_path))
            leaf_name, leaf_item = resolved[-1]
            m = leaf_item.meta
            WVPASS(m.mtime == test_time2)
            WVPASS(leaf_name == 'foo')
            contents = tuple(vfs.contents(repo, leaf_item))
            WVPASS(len(contents) == 3)
            WVPASSEQ(frozenset(name for name, item in contents),
                     frozenset(('.', 'file', 'symlink')))
            for name, item in contents:
                if name == 'file':
                    m = item.meta
                    WVPASS(m.mtime == test_time1)
                elif name == 'symlink':
                    m = item.meta
                    WVPASSEQ(m.symlink_target, 'file')
                    WVPASSEQ(m.size, 4)
                    WVPASSEQ(m.mtime, 0)


def _first_err():
    if helpers.saved_errors:
        return str(helpers.saved_errors[0])
    return ''


@wvtest
def test_from_path_error():
    if is_superuser() or detect_fakeroot():
        return
    with no_lingering_errors():
        with test_tempdir('bup-tmetadata-') as tmpdir:
            path = tmpdir + '/foo'
            os.mkdir(path)
            m = metadata.from_path(path, archive_path=path, save_symlinks=True)
            WVPASSEQ(m.path, path)
            os.chmod(path, 0o000)
            metadata.from_path(path, archive_path=path, save_symlinks=True)
            if metadata.get_linux_file_attr:
                print >> sys.stderr, 'saved_errors:', helpers.saved_errors
                WVPASS(len(helpers.saved_errors) == 1)
                errmsg = _first_err()
                WVPASS(errmsg.startswith('read Linux attr'))
                clear_errors()


def _linux_attr_supported(path):
    # Expects path to denote a regular file or a directory.
    if not metadata.get_linux_file_attr:
        return False
    try:
        metadata.get_linux_file_attr(path)
    except OSError as e:
        if e.errno in (errno.ENOTTY, errno.ENOSYS, errno.EOPNOTSUPP):
            return False
        else:
            raise
    return True


@wvtest
def test_apply_to_path_restricted_access():
    if is_superuser() or detect_fakeroot():
        return
    if sys.platform.startswith('cygwin'):
        return # chmod 000 isn't effective.
    with no_lingering_errors():
        with test_tempdir('bup-tmetadata-') as tmpdir:
            parent = tmpdir + '/foo'
            path = parent + '/bar'
            os.mkdir(parent)
            os.mkdir(path)
            clear_errors()
            m = metadata.from_path(path, archive_path=path, save_symlinks=True)
            WVPASSEQ(m.path, path)
            os.chmod(parent, 0o000)
            m.apply_to_path(path)
            print >> sys.stderr, 'saved_errors:', helpers.saved_errors
            expected_errors = ['utime: ']
            if m.linux_attr and _linux_attr_supported(tmpdir):
                expected_errors.append('Linux chattr: ')
            if metadata.xattr and m.linux_xattr:
                expected_errors.append("xattr.set '")
            WVPASS(len(helpers.saved_errors) == len(expected_errors))
            for i in xrange(len(expected_errors)):
                WVPASS(str(helpers.saved_errors[i]).startswith(expected_errors[i]))
            clear_errors()


@wvtest
def test_restore_over_existing_target():
    with no_lingering_errors():
        with test_tempdir('bup-tmetadata-') as tmpdir:
            path = tmpdir + '/foo'
            os.mkdir(path)
            dir_m = metadata.from_path(path, archive_path=path, save_symlinks=True)
            os.rmdir(path)
            open(path, 'w').close()
            file_m = metadata.from_path(path, archive_path=path, save_symlinks=True)
            # Restore dir over file.
            WVPASSEQ(dir_m.create_path(path, create_symlinks=True), None)
            WVPASS(stat.S_ISDIR(os.stat(path).st_mode))
            # Restore dir over dir.
            WVPASSEQ(dir_m.create_path(path, create_symlinks=True), None)
            WVPASS(stat.S_ISDIR(os.stat(path).st_mode))
            # Restore file over dir.
            WVPASSEQ(file_m.create_path(path, create_symlinks=True), None)
            WVPASS(stat.S_ISREG(os.stat(path).st_mode))
            # Restore file over file.
            WVPASSEQ(file_m.create_path(path, create_symlinks=True), None)
            WVPASS(stat.S_ISREG(os.stat(path).st_mode))
            # Restore file over non-empty dir.
            os.remove(path)
            os.mkdir(path)
            open(path + '/bar', 'w').close()
            WVEXCEPT(Exception, file_m.create_path, path, create_symlinks=True)
            # Restore dir over non-empty dir.
            os.remove(path + '/bar')
            os.mkdir(path + '/bar')
            WVEXCEPT(Exception, dir_m.create_path, path, create_symlinks=True)


from bup.metadata import posix1e
if not posix1e:
    @wvtest
    def POSIX1E_ACL_SUPPORT_IS_MISSING():
        pass


from bup.metadata import xattr
if xattr:
    @wvtest
    def test_handling_of_incorrect_existing_linux_xattrs():
        if not is_superuser() or detect_fakeroot():
            WVMSG('skipping test -- not superuser')
            return
        if not setup_testfs():
            WVMSG('unable to load loop module; skipping dependent tests')
            return
        for f in glob.glob('testfs/*'):
            ex('rm', '-rf', f)
        path = 'testfs/foo'
        open(path, 'w').close()
        xattr.set(path, 'foo', 'bar', namespace=xattr.NS_USER)
        m = metadata.from_path(path, archive_path=path, save_symlinks=True)
        xattr.set(path, 'baz', 'bax', namespace=xattr.NS_USER)
        m.apply_to_path(path, restore_numeric_ids=False)
        WVPASSEQ(xattr.list(path), ['user.foo'])
        WVPASSEQ(xattr.get(path, 'user.foo'), 'bar')
        xattr.set(path, 'foo', 'baz', namespace=xattr.NS_USER)
        m.apply_to_path(path, restore_numeric_ids=False)
        WVPASSEQ(xattr.list(path), ['user.foo'])
        WVPASSEQ(xattr.get(path, 'user.foo'), 'bar')
        xattr.remove(path, 'foo', namespace=xattr.NS_USER)
        m.apply_to_path(path, restore_numeric_ids=False)
        WVPASSEQ(xattr.list(path), ['user.foo'])
        WVPASSEQ(xattr.get(path, 'user.foo'), 'bar')
        os.chdir(start_dir)
        cleanup_testfs()
