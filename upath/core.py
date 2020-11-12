import os
import pathlib
from pathlib import *
import urllib
import re

from fsspec.registry import filesystem

from upath.errors import NotDirectoryError


def argument_upath_self_to_filepath(func):
    '''if arguments are passed to the wrapped function, and if the first
    argument is a UniversalPath instance, that argument is replaced with
    the UniversalPath's path attribute
    '''
    def wrapper(*args, **kwargs):
        if args:
            args = list(args)
            first_arg = args.pop(0)
            if not kwargs.get('path'):
                if isinstance(first_arg, UniversalPath):
                    first_arg = first_arg.path
                    args.insert(0, first_arg)
                args = tuple(args)
        return func(*args, **kwargs)
    return wrapper


class _FSSpecAccessor:

    def __init__(self, parsed_url, *args, **kwargs):
        self._url = parsed_url
        from fsspec.registry import _registry

        self._fs = filesystem(self._url.scheme, **kwargs)

    def __getattribute__(self, item):
        class_attrs = ['_url', '_fs']
        if item in class_attrs:
            x = super().__getattribute__(item)
            return x
        class_methods =['__init__', '__getattribute__'] 
        if item in class_methods:
            return lambda *args, **kwargs: getattr(_FSSpecAccessor, item)(self, *args, **kwargs)
        if item == '__class__':
            return _FSSpecAccessor
        d = object.__getattribute__(self, "__dict__")
        fs = d.get('_fs', None)
        if fs is not None:
            method = getattr(fs, item, None)
            if method:
                return lambda *args, **kwargs: argument_upath_self_to_filepath(method)(*args, **kwargs)
            else:
                raise NotImplementedError(f'{fs.protocol} filesystem has not attribute {item}')


class PureUniversalPath(PurePath):
    _flavour = pathlib._posix_flavour
    __slots__ = ()


class UPath(pathlib.Path):

    def __new__(cls, *args, **kwargs):
        if cls is UPath:
            new_args = list(args)
            first_arg = new_args.pop(0)
            parsed_url = urllib.parse.urlparse(first_arg)
            for key in ['scheme', 'netloc']:
                val = kwargs.get(key)
                if val:
                    parsed_url._replace(**{key: val})
            if not parsed_url.scheme:
                cls = WindowsPath if os.name == 'nt' else PosixPath
            else:
                cls = UniversalPath
                cls._url = parsed_url
                kwargs['_url'] = parsed_url
                cls._kwargs = kwargs
                new_args.insert(0, parsed_url.path)
                args = tuple(new_args)
                
        self = cls._from_parts(args, init=False)
        if not self._flavour.is_supported:
            raise NotImplementedError("cannot instantiate %r on your system"
                                      % (cls.__name__,))
        if cls is UniversalPath:
            self._init(*args, **kwargs)
        else:
            self._init()
        return self


class UniversalPath(Path, PureUniversalPath):

    __slots__ = ('_url', '_kwargs')

    not_implemented = ['cwd', 'home', 'expanduser', 'group', 'is_mount',
                       'is_symlink', 'is_socket', 'is_fifo', 'is_block_device',
                       'is_char_device', 'lchmod', 'lstat', 'owner', 'readlink',
    ]
    

    def _init(self, *args, template=None, **kwargs):
        self._closed = False
        if not self._root and self._parts[0] == '/':
            self._root = self._parts.pop(0)
        if getattr(self, '_str', None):
            delattr(self, '_str')

        if template is not None:
            self._accessor = template._accessor
        else:
            self._accessor = _FSSpecAccessor(self._url, *args, **self._kwargs)

    @classmethod
    def _parse_args(cls, args):
        # This is useful when you don't want to create an instance, just
        # canonicalize some constructor arguments.
        parts = []
        for a in args:
            if isinstance(a, PurePath):
                parts += a._parts
            else:
                a = os.fspath(a)
                if isinstance(a, str):
                    # Force-cast str subclasses to str (issue #21127)
                    parts.append(str(a))
                else:
                    raise TypeError(
                        "argument should be a str object or an os.PathLike "
                        "object returning str, not %r"
                        % type(a))
        return cls._flavour.parse_parts(parts)

    def __getattribute__(self, item):
        if item == '__class__':
            return UniversalPath
        not_implemented = getattr(UniversalPath, 'not_implemented')
        if item in not_implemented:
            raise NotImplementedError(f'UniversalPath has no attribute {item}')
        else:
            return super().__getattribute__(item)

    @classmethod
    def _format_parsed_parts(cls, drv, root, parts):
        join_parts = parts[1:] if parts[0] == '/' else parts
        if (drv or root):
            path = drv + root + cls._flavour.join(join_parts)
        else:
            path = cls._flavour.join(join_parts)
        scheme, netloc = cls._url.scheme, cls._url.netloc
        scheme = scheme + ':'
        netloc = '//' + netloc if netloc else ''
        formatted = scheme + netloc + path
        return formatted

    @property
    def path(self):
        if self._parts:
            join_parts = self._parts[1:] if self._parts[0] == '/' else self._parts
            path = self._flavour.join(join_parts)
            return self._root + path
        else:
            return '/'

    def open(self, *args, **kwargs):
        return self._accessor.open(self, *args, **kwargs)

    def iterdir(self):
        """Iterate over the files in this directory.  Does not yield any
        result for the special paths '.' and '..'.
        """
        if self._closed:
            self._raise_closed()
        for name in self._accessor.listdir(self):
            # fsspec returns dictionaries
            if isinstance(name, dict):
                name = name.get('name')
            if name in {'.', '..'}:
                # Yielding a path object for these makes little sense
                continue
            # only want the path name with iterdir
            name = re.sub(f'^{self.path}/', '', name)
            yield self._make_child_relpath(name)
            if self._closed:
                self._raise_closed()

    def exists(self):
        """
        Whether this path exists.
        """
        try:
            self._accessor.stat(self)
        except FileNotFoundError:
            return False
        return True

    def is_dir(self):
        info = self._accessor.info(self)
        if info['type'] == 'directory':
            return True
        return False

    def is_file(self):
        info = self._accessor.info(self)
        if info['type'] == 'file':
            return True
        return False

    def glob(self, pattern):
        path = self.joinpath(pattern)
        return self._accessor.glob(self, path=path)

    def rename(self, target):
        # can be implimented, but may be tricky
        raise NotImplementedError

    def touch(self, trunicate=True, **kwargs):
        self._accessor.touch(self, trunicate=trunicate, **kwargs)

    def unlink(self, missing_ok=False):
        if not self.exists():
            if not missing_ok:
                raise FileNotFoundError
            else:
                return
        try:
            self._accessor.rm_file(self)
        except:
            self._accessor.rm(self, recursive=False)

    def rmdir(self, recursive=True):
        '''Add warning if directory not empty
        assert is_dir?
        '''
        try:
            assert self.is_dir()
        except:
            raise NotDirectoryError
        self._accessor.rm(self, recursive=recursive)
        

        
