"""Framework-independent environment manager; compatible with Python 3.10.

This is process/dependency isolation, not a sandbox for untrusted plugins.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
from datetime import datetime, timezone


class RuntimeError(Exception):
    pass


class RuntimeManager:
    def __init__(self, state_root, env_root, python, uv, timeout=300, install_timeout=900):
        self.state_root = Path(state_root)
        self.env_root = self.absolute(env_root)
        self.python = self.executable_path(python)
        self.uv = str(self.absolute(uv))
        self.timeout = self.seconds(timeout)
        self.install_timeout = self.seconds(install_timeout)

    @classmethod
    def from_settings(cls, get, path_data):
        root = Path(__file__).parent
        return cls(Path(path_data) / 'python_runtime',
                   get('runtime_env_root') or str(Path(path_data) / 'python_runtime' / 'envs'),
                   get('runtime_python') or str(root / '.tools/python3.14'),
                   get('runtime_uv') or str(root / '.tools/uv'),
                   get('runtime_timeout') or 300, get('runtime_install_timeout') or 900)

    @staticmethod
    def executable_path(value):
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise RuntimeError('Python 실행 파일은 절대 경로로 지정하세요.')
        # Resolving a venv's python symlink would silently select its base Python.
        return os.path.abspath(path)

    @staticmethod
    def absolute(value):
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise RuntimeError('컨테이너 내부의 절대 경로를 입력하세요.')
        return path.resolve()

    @staticmethod
    def seconds(value):
        value = int(value)
        if not 1 <= value <= 86400:
            raise RuntimeError('제한 시간은 1~86400초로 설정하세요.')
        return value

    @staticmethod
    def identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}', value):
            raise RuntimeError('이름은 영문·숫자로 시작하는 1~64자의 영문·숫자·._-만 가능합니다.')
        return value

    @contextlib.contextmanager
    def lock(self, name, shared=False):
        self.state_root.mkdir(parents=True, exist_ok=True)
        with (self.state_root / (name + '.lock')).open('a') as fp:
            try:
                fcntl.flock(fp, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('다른 작업이 이 환경을 사용 중입니다. 잠시 후 다시 시도하세요.')
            try:
                yield
            finally:
                fcntl.flock(fp, fcntl.LOCK_UN)

    def read(self):
        path = self.state_root / 'registry.json'
        return json.loads(path.read_text()) if path.exists() else {'interpreters': [], 'environments': {}}

    def write(self, data):
        self.state_root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.state_root, prefix='.registry-')
        try:
            with os.fdopen(fd, 'w') as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(name, self.state_root / 'registry.json')
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def execute(self, args, *, timeout=None, input=None, cwd=None, env=None):
        limit = self.seconds(self.timeout if timeout is None else timeout)
        child_env = {k: v for k, v in os.environ.items()
                     if not k.startswith('PYTHON') and k not in ('VIRTUAL_ENV', 'FF_PYTHON')}
        if env:
            if any(k.startswith('PYTHON') or k == 'VIRTUAL_ENV' for k in env):
                raise RuntimeError('PYTHON* 및 VIRTUAL_ENV 환경 변수는 직접 지정할 수 없습니다.')
            child_env.update(env)
        if (Path(args[0]).parent.parent / 'pyvenv.cfg').is_file():
            child_env['VIRTUAL_ENV'] = str(Path(args[0]).parent.parent)
            child_env['PATH'] = str(Path(args[0]).parent) + os.pathsep + child_env.get('PATH', '')
        try:
            p = subprocess.Popen([str(x) for x in args], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding='utf-8', errors='replace',
                                 start_new_session=True, cwd=cwd, env=child_env)
        except OSError as exc:
            raise RuntimeError(str(exc)) from exc
        try:
            stdout, stderr = p.communicate(input, timeout=limit)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.communicate()
            raise RuntimeError('작업 제한 시간을 초과하여 프로세스를 종료했습니다.')
        return {'returncode': p.returncode, 'stdout': stdout, 'stderr': stderr}

    def checked(self, args, **kwargs):
        result = self.execute(args, **kwargs)
        if result['returncode']:
            raise RuntimeError((result['stderr'] or result['stdout'] or '프로세스 실행 실패')[-4000:])
        return result

    def probe(self, path):
        path = self.executable_path(path)
        result = self.checked([path, '-I', '-c',
            'import json,sys; print(json.dumps({"version":sys.version.split()[0],'
            '"implementation":sys.implementation.name,"prefix":sys.prefix,"base_prefix":sys.base_prefix}))'], timeout=15)
        return dict(json.loads(result['stdout']), path=path)

    def interpreters(self):
        candidates = [self.python] + self.read()['interpreters']
        candidates += [str(p) for p in (Path(__file__).parent / '.runtime/python').glob('*/bin/python3.*')
                       if re.fullmatch(r'python3\.\d+', p.name)]
        found = {}
        for candidate in candidates:
            path = self.executable_path(candidate)
            canonical = os.path.realpath(path)
            if canonical in found:
                continue
            try:
                info = self.probe(path)
                if info['prefix'] != info['base_prefix']:
                    continue
                found[canonical] = info
            except (RuntimeError, ValueError):
                continue
        return list(found.values())

    def register_python(self, path):
        info = self.probe(path)
        if info['prefix'] != info['base_prefix']:
            raise RuntimeError('가상 환경이 아닌 기본 Python 실행 파일을 등록하세요.')
        with self.lock('registry'):
            data = self.read()
            if info['path'] not in data['interpreters']:
                data['interpreters'].append(info['path'])
                self.write(data)
        return info

    def environments(self):
        return [dict(item, ready=(Path(item['path']) / 'pyvenv.cfg').is_file()
                     and (Path(item['path']) / 'bin/python').exists())
                for item in self.read()['environments'].values()]

    def environment(self, owner, name='default'):
        key = self.identifier(owner) + '/' + self.identifier(name)
        item = self.read()['environments'].get(key)
        if item is None:
            raise RuntimeError('등록되지 않은 가상 환경입니다.')
        return item

    def ensure_environment(self, owner, name='default', *, python=None, path=None):
        key = self.identifier(owner) + '/' + self.identifier(name)
        with self.lock('registry'):
            data = self.read()
            existing = data['environments'].get(key)
            if existing:
                if ((path and str(self.absolute(path)) != existing['path']) or
                        (python and self.executable_path(python) != existing['python'])):
                    raise RuntimeError('기존 환경의 경로·Python은 바꿀 수 없습니다. 새 이름으로 생성하세요.')
                if not (Path(existing['path']) / 'bin/python').exists():
                    raise RuntimeError('환경 파일이 없습니다. 경로와 마운트를 확인하세요.')
                return existing
            interpreter = self.probe(python or self.python)
            if interpreter['prefix'] != interpreter['base_prefix']:
                raise RuntimeError('기본 Python 실행 파일을 선택하세요.')
            target = self.absolute(path) if path else self.env_root / owner / name
            # Never adopt or overwrite an arbitrary directory, including an empty one.
            if target.exists() or target.is_symlink():
                raise RuntimeError('이미 존재하는 경로입니다. 새 디렉터리 경로를 지정하세요.')
            target.parent.mkdir(parents=True, exist_ok=True)
            self.checked([interpreter['path'], '-I', '-m', 'venv', str(target)], timeout=self.install_timeout)
            item = {'owner': owner, 'name': name, 'path': str(target),
                    'python': interpreter['path'], 'version': interpreter['version'],
                    'created_at': datetime.now(timezone.utc).isoformat()}
            data['environments'][key] = item
            self.write(data)
            return item

    def run_python(self, owner, args, *, name='default', **kwargs):
        item = self.environment(owner, name)
        with self.lock(self.env_lock(owner, name), shared=True):
            return self.execute([str(Path(item['path']) / 'bin/python'), *args], **kwargs)

    def run_module(self, owner, module, args=(), *, name='default', **kwargs):
        return self.run_python(owner, ['-m', module, *args], name=name, **kwargs)

    def run_executable(self, owner, executable, args=(), *, name='default', **kwargs):
        self.identifier(executable)
        item = self.environment(owner, name)
        with self.lock(self.env_lock(owner, name), shared=True):
            return self.execute([str(Path(item['path']) / 'bin' / executable), *args], **kwargs)

    def install_packages(self, owner, packages, *, name='default'):
        if not packages or any(not isinstance(p, str) or p.startswith('-') for p in packages):
            raise RuntimeError('패키지 지정 목록을 전달하세요. pip 옵션은 허용하지 않습니다.')
        item = self.environment(owner, name)
        with self.lock(self.env_lock(owner, name)):
            return self.checked([self.uv, 'pip', 'install', '--python',
                                 str(Path(item['path']) / 'bin/python'), '--', *packages],
                                timeout=self.install_timeout)

    @staticmethod
    def env_lock(owner, name):
        return 'env-' + hashlib.sha256((owner + '/' + name).encode()).hexdigest()
