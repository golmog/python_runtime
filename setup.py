from plugin import create_plugin_instance

P = create_plugin_instance({
    'filepath': __file__, 'use_db': True, 'use_default_setting': True,
    'home_module': 'runtime', 'default_route': 'normal',
    'menu': {'uri': 'python_runtime', 'name': 'Python Runtime', 'list': [
        {'uri': 'runtime', 'name': '런타임', 'list': [
            {'uri': 'setting', 'name': '설정'},
            {'uri': 'environments', 'name': '가상 환경'},
        ]},
        {'uri': 'manual', 'name': '사용 안내', 'list': [
            {'uri': 'README.md', 'name': 'README'},
        ]},
        {'uri': 'log', 'name': '로그'},
    ]},
})
from .mod_runtime import ModuleRuntime
P.set_module_list([ModuleRuntime])
