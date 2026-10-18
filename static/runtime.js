/* Uses FF's authenticated command route; never builds HTML from process output. */
$(function () {
  let packages = [], packageTargets = [], polling = null;
  function message(text, failed) {
    $('#runtime_message').removeClass('alert-info alert-danger').addClass(failed ? 'alert-danger' : 'alert-info').text(text);
  }
  function command(name, arg1, arg2, arg3) {
    return $.ajax({url: '/python_runtime/ajax/runtime/command', method: 'POST', dataType: 'json',
      data: {command: name, arg1: arg1 || '', arg2: arg2 || '', arg3: arg3 || ''}});
  }
  function error(xhr) { message('요청 실패: ' + xhr.status + '. 로그인 상태와 로그를 확인하세요.', true); }
  function action(name, a, b, c) {
    return command(name, a, b, c).done(function (r) {
      message(r.msg || '완료', r.ret !== 'success');
      if (r.ret === 'success') {
        if ((name === 'create' || name === 'install') && !polling) polling = setInterval(refresh, 2000);
        refresh();
      }
    }).fail(error);
  }
  function selectPython(id, items) {
    const el = $(id); if (!el.length) return;
    const selected = el.val() || el.data('selected'); el.empty();
    items.forEach(i => el.append($('<option>').val(i.path).text(i.version + ' — ' + i.path)));
    if (items.some(i => i.path === selected)) el.val(selected);
  }
  function refresh() {
    command('status').done(function (r) {
      if (r.ret !== 'success') { message(r.msg, true); return; }
      selectPython('#runtime_python', r.interpreters); selectPython('#env_python', r.interpreters);
      $('#interpreters').empty();
      r.interpreters.forEach(i => $('#interpreters').append($('<p>').text('Python ' + i.version + ' · ' + i.path)));
      $('#environments').empty();
      if (!r.environments.length) $('#environments').append($('<tr>').append($('<td colspan="5">').text('등록된 가상 환경이 없습니다.')));
      r.environments.forEach(e => {
        const buttons = $('<td>');
        $('<button class="btn btn-sm btn-outline-primary mr-1">').text('실행 확인').on('click', () => action('check', e.owner, e.name)).appendTo(buttons);
        $('<button class="btn btn-sm btn-outline-secondary">').text('패키지').on('click', () => {
          const index = packageTargets.findIndex(t => t.kind === 'env' && t.a === e.owner && t.b === e.name);
          $('#package_target').val(String(index)); loadPackages();
        }).appendTo(buttons);
        $('#environments').append($('<tr>').append($('<td>').text(e.owner + ' / ' + e.name), $('<td>').text(e.version),
          $('<td style="overflow-wrap:anywhere">').text(e.path), $('<td>').text(e.ready ? '준비됨' : '경로 확인 필요'), buttons));
      });
      const installSelect = $('#install_target'), oldInstall = installSelect.val();
      installSelect.empty();
      r.environments.filter(e => e.ready).forEach(e => installSelect.append($('<option>')
        .val(JSON.stringify({owner: e.owner, name: e.name})).text(e.owner + ' / ' + e.name + ' · Python ' + e.version)));
      if (oldInstall && installSelect.find('option').toArray().some(o => o.value === oldInstall)) installSelect.val(oldInstall);
      $('#install_button').prop('disabled', r.job.running || !installSelect.find('option').length);
      const oldTarget = packageTargets[Number($('#package_target').val())];
      packageTargets = [{kind: 'ff', label: 'FF 기본 Python ' + r.ff_version}];
      r.interpreters.forEach(i => packageTargets.push({kind: 'python', a: i.path, label: 'Python ' + i.version + ' · ' + i.path}));
      r.environments.forEach(e => packageTargets.push({kind: 'env', a: e.owner, b: e.name, label: e.owner + ' / ' + e.name + ' · ' + e.version}));
      const target = $('#package_target').empty();
      packageTargets.forEach((t, i) => target.append($('<option>').val(i).text(t.label)));
      const preferred = r.interpreters.find(i => /^3\.14(?:\.|$)/.test(i.version)) || r.interpreters[0];
      if (preferred) target.val(String(packageTargets.findIndex(t => t.kind === 'python' && t.a === preferred.path)));
      if (oldTarget) { const idx = packageTargets.findIndex(t => t.kind === oldTarget.kind && t.a === oldTarget.a && t.b === oldTarget.b); if (idx >= 0) target.val(String(idx)); }
      $('#create_button').prop('disabled', r.job.running || !r.interpreters.length);
      if (r.job.running) { message(r.job.message); if (!polling) polling = setInterval(refresh, 2000); }
      else { if (polling) { clearInterval(polling); polling = null; message(r.job.message, r.job.ok === false); loadPackages(); }
        else if (!$('#runtime_message').data('loaded')) message('FF Python ' + r.ff_version + ' · 사용 가능한 Python ' + r.interpreters.length + '개 · 가상 환경 ' + r.environments.length + '개'); }
      $('#runtime_message').data('loaded', true);
    }).fail(error);
  }
  $('#runtime_settings').on('submit', function(e) { e.preventDefault(); const values = {}; $(this).serializeArray().forEach(x => values[x.name] = x.value); action('save', JSON.stringify(values)); });
  $('#register_form').on('submit', function(e) { e.preventDefault(); action('register', $('#register_path').val()); });
  $('#create_form').on('submit', function(e) { e.preventDefault(); action('create', JSON.stringify({owner: $('#owner').val(), name: $('#env_name').val(), python: $('#env_python').val(), path: $('#env_path').val()})); });
  $('#install_form').on('submit', function(e) {
    e.preventDefault(); if (!$('#install_target').val()) return;
    const target = JSON.parse($('#install_target').val());
    const index = packageTargets.findIndex(t => t.kind === 'env' && t.a === target.owner && t.b === target.name);
    $('#package_target').val(String(index));
    packages = []; renderPackages(); $('#package_info').text('설치 진행 중…');
    action('install', JSON.stringify({owner: target.owner, name: target.name, packages: $('#install_packages').val()}));
  });
  $('#refresh').on('click', refresh);
  function renderPackages() {
    const query = $('#package_filter').val().toLowerCase(); $('#packages').empty();
    packages.filter(p => p.name.toLowerCase().includes(query)).forEach(p => $('#packages').append($('<tr>').append($('<td>').text(p.name), $('<td>').text(p.version))));
  }
  function loadPackages() {
    if (!$('#package_target').length) return;
    const t = packageTargets[Number($('#package_target').val())]; if (!t) return;
    $('#load_packages').prop('disabled', true); $('#package_info').text('조회 중…');
    command('packages', t.kind, t.a, t.b).done(r => {
      packages = r.ret === 'success' ? r.packages : []; renderPackages();
      $('#package_info').text(r.ret === 'success' ? t.label + ' · ' + packages.length + '개 · ' + r.executable : r.msg);
    }).fail(error).always(() => $('#load_packages').prop('disabled', false));
  }
  $('#load_packages').on('click', loadPackages); $('#package_filter').on('input', renderPackages);
  $('#package_target').on('change', function () {
    packages = []; renderPackages(); $('#package_info').text('선택한 환경의 패키지를 조회하세요.');
  });
  refresh();
});
