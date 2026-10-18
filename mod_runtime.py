import json
import os
import sys
import threading
from pathlib import Path

from flask import jsonify, redirect, render_template
from framework import F
from plugin import PluginModuleBase
from .runtime import RuntimeManager, RuntimeError


class ModuleRuntime(PluginModuleBase):
    db_default = {
        'runtime_python': str(Path(__file__).parent / '.tools/python3.14'),
        'runtime_uv': str(Path(__file__).parent / '.tools/uv'),
        'runtime_env_root': os.path.join(F.config['path_data'], 'python_runtime', 'envs'),
        'runtime_timeout': '300', 'runtime_install_timeout': '900',
    }

    def __init__(self, P):
        super().__init__(P, name='runtime', first_menu='setting')
        self.job = {'running': False, 'message': ''}
        self.job_lock = threading.Lock()

    def manager(self):
        return RuntimeManager.from_settings(self.P.ModelSetting.get, F.config['path_data'])

    def process_menu(self, page, req):
        if page == 'guide':
            return redirect('/python_runtime/manual/README.md')
        if page not in ('setting', 'environments'):
            page = 'setting'
        return render_template('python_runtime_setting.html', page=page,
                               arg=self.P.ModelSetting.to_dict())

    def background(self, label, function):
        with self.job_lock:
            if self.job['running']:
                raise RuntimeError('진행 중인 작업이 있습니다.')
            self.job = {'running': True, 'message': label, 'ok': None}
        def work():
            try:
                function()
                self.job = {'running': False, 'message': label + ' 완료', 'ok': True}
                self.P.logger.info('%s 완료', label)
            except Exception as exc:
                self.job = {'running': False, 'message': str(exc), 'ok': False}
                self.P.logger.warning('%s 실패: %s', label, exc)
        threading.Thread(target=work, daemon=True).start()
        return {'ret': 'success', 'msg': label + ' 시작'}

    def process_command(self, command, arg1, arg2, arg3, req):
        try:
            m = self.manager()
            if command == 'status':
                return jsonify(ret='success', interpreters=m.interpreters(),
                               environments=m.environments(), job=self.job,
                               ff_python=sys.executable, ff_version=sys.version.split()[0])
            if command == 'save':
                values = json.loads(arg1)
                python = m.executable_path(values['runtime_python'])
                info = m.probe(python)
                if info['prefix'] != info['base_prefix']:
                    raise RuntimeError('기본 Python 실행 파일을 선택하세요.')
                root = str(m.absolute(values['runtime_env_root']))
                uv = str(m.absolute(values['runtime_uv']))
                if not os.path.isfile(uv) or not os.access(uv, os.X_OK):
                    raise RuntimeError('uv 실행 파일 경로를 확인하세요.')
                timeout = m.seconds(values['runtime_timeout'])
                install_timeout = m.seconds(values['runtime_install_timeout'])
                for key, value in dict(runtime_python=python, runtime_env_root=root,
                                       runtime_uv=uv, runtime_timeout=timeout,
                                       runtime_install_timeout=install_timeout).items():
                    self.P.ModelSetting.set(key, str(value))
                return jsonify(ret='success', msg='설정을 저장했습니다. 기존 환경의 경로는 유지됩니다.')
            if command == 'register':
                info = m.register_python(arg1)
                return jsonify(ret='success', msg='Python ' + info['version'] + ' 등록 완료')
            if command == 'create':
                values = json.loads(arg1)
                return jsonify(self.background('가상 환경 생성', lambda: m.ensure_environment(
                    values['owner'], values['name'], python=values.get('python') or None,
                    path=values.get('path') or None)))
            if command == 'install':
                values = json.loads(arg1)
                owner, name = values['owner'], values['name']
                m.environment(owner, name)
                packages = [line.strip() for line in values['packages'].splitlines()
                            if line.strip() and not line.strip().startswith('#')]
                if not packages or len(packages) > 100 or any(p.startswith('-') for p in packages):
                    raise RuntimeError('패키지를 한 줄에 하나씩 입력하세요 (최대 100개). pip 옵션은 지원하지 않습니다.')
                return jsonify(self.background('패키지 설치: ' + owner + '/' + name,
                    lambda: m.install_packages(owner, packages, name=name)))
            if command == 'packages':
                if arg1 == 'ff':
                    executable = sys.executable
                elif arg1 == 'python':
                    executable = arg2
                    if executable not in [i['path'] for i in m.interpreters()]:
                        raise RuntimeError('등록된 Python을 선택하세요.')
                else:
                    item = m.environment(arg2, arg3)
                    executable = str(Path(item['path']) / 'bin/python')
                # importlib.metadata works without pip and reports the actual selected environment.
                code = ('import importlib.metadata as m,json; '
                        'print(json.dumps(sorted([{"name":d.metadata.get("Name","unknown"),'
                        '"version":d.version} for d in m.distributions()],key=lambda d:d["name"].lower())))')
                result = m.checked([executable, '-I', '-c', code], timeout=30)
                return jsonify(ret='success', packages=json.loads(result['stdout']), executable=executable)
            if command == 'check':
                result = m.run_python(arg1, ['-I', '-c',
                    'import sys,json; print(json.dumps({"version":sys.version.split()[0],"executable":sys.executable,"prefix":sys.prefix}))'],
                    name=arg2, timeout=15)
                if result['returncode']:
                    raise RuntimeError(result['stderr'][-2000:])
                return jsonify(ret='success', msg=result['stdout'])
            raise RuntimeError('지원하지 않는 명령입니다.')
        except Exception as exc:
            return jsonify(ret='warning', msg=str(exc))
