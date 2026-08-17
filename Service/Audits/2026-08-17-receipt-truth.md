# Аудит правдивости receipt — 2026-08-17

Мета:

- Репозиторий: `D:\ZAI\MCP\Grok CLI` (`zai-one/grok-mcp`)
- HEAD: `e79e0bf1f173c19173ab855248f170757f63b9dc` (`e79e0bf docs(audits): a home for independent audit findings`)
- Ветка: `main`
- Версия моста: `0.10.0` (`grok_delegate/guard.py` `SERVER_VERSION`, `pyproject.toml`)
- Читал: `AGENTS.md`, `Service/Handoffs/grok-mcp-production-ready-evidence.md`, `schemas/grok-work-receipt.v1.schema.json`, `grok_delegate/contracts.py` (`finalize_receipt`, `validate_task_packet`), `grok_delegate/agent_runtime.py` (`run_task`, `_changes_since`, `_path_state`, `_resolve_base_sha`, `_verify_or_explain`, `_run_bridge_tests`, `_present_artifacts`), `grok_delegate/runner.py` (`collect_diff`, `commit_lane_work`, `_bounded_unified_diff`), `grok_delegate/economy.py` (`compact_job_record`), `grok_delegate/jobs_store.py` (усечение), `grok_delegate/jobs.py` (state), `grok_delegate/server.py` (poll), тесты `tests/test_round8_bridge.py`, `tests/test_lane_commit_and_verification.py`, `tests/test_economy.py`, `tests/test_base_ref_and_receipt_reason.py`, `tests/test_grok_delegate.py` (cap unified_diff)
- Гонял: изолированные репро с фейковым адаптером в `%TEMP%\grokmcp-receipt\` (`C:\Users\codex\AppData\Local\Temp\grokmcp-receipt\`). Скрипты: `run_scenarios.py`, `followup.py`, `s06_tracked.py`. Доказательства: `s01_stale_artifact.json` … `s10_tests_skip.json`, `s09b_commit_failed_live.json`, `s09_compact.json`, `followup.json`, `s06_tracked.json`. Живой Grok CLI / MCP-сервер не вызывались. Дерево `Grok CLI` не менялось, кроме этого файла.

Краткий вердикт. Receipt **способен соврать**, и не только «в сторону отказа». Гейт `UNEXPECTED_CHANGED_FILES` не срабатывает, когда `changed_files` пуст: `no_changes` коротко замыкается раньше, после чего мост всё равно делает `git add -A` и коммитит leftover, в том числе чужой `attacker.py`. Следующий job на той же lane даёт `status=completed` / `ok=true` без этого файла в `changed_files` и `full_changed_files` — хост, который мержит `grok/*` по зелёному receipt, забирает отвергнутую работу. Verifier может сам создать `expected_artifacts` и сертифицировать job как успех worker'а. Для новых файлов `unified_diff` в completed-receipt пуст: снимок берётся до lane-commit, а `git diff` не видит untracked. Артефакт в новосозданном каталоге (`src/app.py`) ложно блокируется как `UNEXPECTED_CHANGED_FILES: src`. Компактный poll (дешёвый цикл) не поднимает `result.ok`, оборачивает ответ в `ok: true` и выбрасывает `lane_commit`/`commits` — `COMMIT_FAILED` при этом выглядит как сданный job. Закрытые в evidence дыры (stale expected не сертифицирует чужой diff, revert теста → `no_changes`, `DIFF_SNAPSHOT_FAILED` ≠ `no_changes`, pin `base_ref` внутри одного job) на этих репро держатся.

## Находки

### F-1 `no_changes` коммитит leftover; следующий completed прячет его с ветки

- Серьёзность: **blocker**
- Статус: подтверждено репро
- Где: `grok_delegate/contracts.py:284-287` (короткое замыкание до `UNEXPECTED_CHANGED_FILES`), `grok_delegate/agent_runtime.py:506-516` (commit после гейта), `grok_delegate/runner.py:843` (`git add -A`)
- Что receipt утверждает: job 1 — `status=no_changes`, `ok=false`, `EXECUTE_NO_CHANGES` (хост должен отвергнуть). Job 2 — `status=completed`, `ok=true`, `changed_files=["expected.txt"]`, `full_changed_files=["expected.txt"]` (хост должен принять lane).
- Что произошло на самом деле: в lane до job 1 лежал untracked `attacker.py`. Job 1 worker ничего не писал. Гейт увидел пустой `changed_files` (контент совпал с pre-run snapshot) и **не дошёл** до проверки `full_changed_files=["attacker.py"]`. Затем мост закоммитил `attacker.py` в `grok/*`. Job 2 написал `expected.txt`; база — уже HEAD lane (коммит с attacker), поэтому attacker не попадает ни в один список. `git ls-tree HEAD` job 2: `attacker.py` есть.
- Репро:

```
py -3 C:\Users\codex\AppData\Local\Temp\grokmcp-receipt\followup.py
```

Фрагмент `followup.json`:

```
job1: status=no_changes, full_changed_files=["attacker.py"],
      lane_commit.committed=true, sha=45e8f58…
job1_head_show: "… worker output for s11-job1\nattacker.py"
job2: status=completed, ok=true, changed_files=["expected.txt"],
      attacker_in_job2_changed=false, attacker_in_job2_full=false,
      attacker_still_in_lane_tree=true
job2_tree: [".gitignore", "attacker.py", "expected.txt", "test_acceptance.py"]
```

Существующий тест `test_reused_worktree_preexisting_unexpected_diff_blocks_new_run` закрывает другой вход: leftover + **этот** run ещё и пишет expected, тогда `changed_files` непуст и `UNEXPECTED` срабатывает. Дыра — leftover + **noop**.
- Последствие для оператора: отвергнутый job всё равно кладёт мусор в `grok/*`. Следующий зелёный receipt — разрешение смержить этот мусор. Цена ошибки, если находка неверна: ложная тревога на reuse lane; проверяется одним `git log master..HEAD` / `git ls-tree`.

Связанный вход (тот же short-circuit): worker **удалил** untracked leftover `expected.txt` → `status=no_changes`, `changed_files=[]`, файла на диске нет (`s02_delete.json` / `untracked_leftover`). Удаление невидимо. Tracked-удаление, наоборот, честное: `EXPECTED_ARTIFACT_MISSING`, путь в `changed_files`.

### F-2 Verifier может изготовить expected-артефакт; receipt — `completed`

- Серьёзность: **major**
- Статус: подтверждено репро
- Где: `grok_delegate/agent_runtime.py:485-496` (переснимок **после** verifier), `grok_delegate/contracts.py:289-311` (наличие + «изменился в этом run» без автора)
- Что receipt утверждает: `ok=true`, `status=completed`, `changed_files=["expected.txt","extra.txt"]`, `artifacts` оба, tests `passed=true` / `source=bridge-verifier`. Хост читает это как «worker сделал оба артефакта, тесты моста зелёные».
- Что произошло на самом деле: адаптер писал только `expected.txt` (`WORKER\n`). Unittest сам создал `extra.txt` с `FROM_TEST\n`. Мост приписал оба пути этому run, потому что `_changes_since` сравнивает с pre-worker snapshot, а не с pre-test.
- Репро: `run_scenarios.py` сценарий s03b. Доказательство: `s03_test_mutates.json` → `test_creates_expected`.

```
ok: true, status: completed
changed_files: ["expected.txt", "extra.txt"]
extra_content: "FROM_TEST\n"
worker_wrote_extra: false
```

Контроль рядом: тот же verifier, но файл **не** в `expected_artifacts` (`noise.log`) → `UNEXPECTED_CHANGED_FILES: noise.log`, `ok=false`. Гейт ловит лишнее, не ловит «тест дописал обязательное».
- Последствие для оператора: примет работу, которую сделал pytest/unittest, не worker. Цена ложной находки: «так и задумано, acceptance = дерево после verifier» — тогда это дыра в атрибуции, а не в гейте revert (revert по-прежнему даёт `no_changes`, см. evidence).

Следствие того же переснимка: тест **переписывает** содержимое артефакта (`WORKER` → `FROM_TEST`) — receipt `completed`, на диске текст теста (`test_rewrites_artifact`). Это зеркало закрытого бага «тест откатил → stale completed»: откат честно гасится, подмена содержимого проходит.

### F-3 `unified_diff` пуст у completed job с новыми файлами

- Серьёзность: **major**
- Статус: подтверждено репро
- Где: `grok_delegate/runner.py:999-1015` (`git diff HEAD` + `git diff base...HEAD`, untracked нет), `grok_delegate/agent_runtime.py:488-536` (снимок **до** `commit_lane_work`)
- Что receipt утверждает: `status=completed`, `changed_files=["expected.txt"]`, `unified_diff=""` — для хоста, который не открывает репозиторий, «смотреть нечего» / контент недоступен. Схема (`grok-work-receipt.v1`) говорит, что `unified_diff` покрывает `full_changed_files`.
- Что произошло на самом деле: файл есть, lane-commit есть, `git diff master...HEAD` после job даёт 138 байт с содержимым. Receipt снят, пока файл был untracked.
- Репро: `followup.py` блок `unified_diff_after_commit`; также `s06_diff_cap.json` (80 КБ **новый** файл, `unified_diff_len=0`) против `s06_tracked.json` (тот же объём в **уже tracked** файле — diff 16399 байт с маркером).
- Последствие для оператора: либо открывает worktree (сервис не экономит токены), либо принимает `completed` вслепую. Не ложный успех пустой работы — `changed_files` честный — но главная обещанная «картинка работы» пустая как раз в типичном execute (создать файл). Цена ошибки: «разные срезы, так в схеме» — да, 1:1 не обещали; пустой diff при непустом `changed_files` на **новом** файле схема не описывает.

На **tracked** изменении усечение видно: `…(truncated)`, cap 16384 (`s06_tracked.json`, `runner.py:785-791`). Это не находка.

### F-4 Артефакт в новосозданном каталоге ложно блокируется

- Серьёзность: **major**
- Статус: подтверждено репро
- Где: `grok_delegate/runner.py:943-951` (porcelain: untracked directory схлопывается в `src/`), `grok_delegate/contracts.py:304-311` (сравнение `src` с expected `src/app.py`)
- Что receipt утверждает: `status=blocked`, `UNEXPECTED_CHANGED_FILES: src`. Хост думает, worker насоздавал лишнее.
- Что произошло на самом деле: worker создал ровно `src/app.py`. Файл на диске, `_present_artifacts` его видит (`artifacts=["src/app.py"]`). Git status без `-uall` репортит `?? src/`. `changed_files=["src/"]`. После `rstrip("/")` это `src` ∉ `{src/app.py}` → unexpected. Последний гейт затирает `EXPECTED_ARTIFACT_NOT_CHANGED`.
- Репро: `followup.py` `nested_new_dir`. Контроль: каталог `src/` уже в baseline (`nested_existing_dir`) → `completed`, `changed_files=["src/app.py"]`.
- Последствие для оператора: отвергнет валидную работу (новый пакет, `outputs/…`, любой путь, чей parent ещё не tracked). Повтор job жжёт бюджет. Цена ошибки: «надо указывать каталог» — нет: expected=`outdir` тоже ломается, потому что `_present_artifacts` принимает только файлы (`agent_runtime.py:862-863`) → `EXPECTED_ARTIFACT_MISSING: outdir` при живом `outdir/result.txt` (`s05_artifact_paths.json` / `directory`).

Нормализация `a\b` → `a/b` в `validate_task_packet` работает (`s05 slash` / `normalized_expected=["sub/file.txt"]`); падение там — этот же porcelain, не слэши.

### F-5 `completed` при `COMMIT_FAILED`; compact poll прячет это и ставит `ok: true`

- Серьёзность: **major**
- Статус: подтверждено репро
- Где: `grok_delegate/agent_runtime.py:506-551` (`lane_commit` пишется в receipt, `finalize_receipt` его не читает), `grok_delegate/contracts.py:336` (`ok` только от `status==completed`), `grok_delegate/economy.py:105-141` (`lane_commit`/`commits`/`ok` не в keep/lift; пустые списки не поднимаются), `grok_delegate/server.py:1157-1158` (`return {"ok": True, **compact}`)
- Что receipt утверждает:
  - полный: `ok=true`, `status=completed`, и только в стороне `lane_commit.committed=false`, `reason=COMMIT_FAILED`;
  - compact poll (дешёвый цикл, `GROK_DELEGATE_ECONOMY_COMPACT_POLL`): `ok=true` (обёртка poll), `status=completed`, поля `lane_commit` нет. Хост, который не открывает git, считает работу лежащей на `grok/*`.
- Что произошло на самом деле: `git status --porcelain` после job — `?? expected.txt`. Коммита нет. Worktree-only; janitor это снимет.
- Репро: `s09b_commit_failed_live.json` (подмена `commit_lane_work` в процессе, не в репозитории):

```
full: ok=true, status=completed,
      lane_commit={ok:false, committed:false, reason:COMMIT_FAILED}
git_status: "?? expected.txt\n"
poll_ok: true, poll_has_lane_commit: false
```

Тот же compact на job-конверте с `result.ok=false` / `status=no_changes` (`s09_compact.json`): `poll_ok=true`, `receipt_ok_in_compact=null` (`ok` не lifted), нет `changed_files`/`tests`/`commits`/`full_changed_files`/`lane_commit` (пустой список не поднимается; ключей нет в keep). `status` и `state=error` ещё видны — кто читает `status`, не купится; кто читает `ok` как в комментарии `mark_empty_execute_lane` — купится.
- Последствие для оператора: примет «сдано и закоммичено», потеряет работу вместе с worktree; либо примет отвергнутый job по `ok: true`. Цена ошибки: «`ok` у poll значит “job найден”» — тогда это двусмысленность контракта, но `lane_commit` всё равно исчезает у единственного цикла, которым хост пользуется.

Молчаливое усечение списков до 24 без маркера (`economy.py:150-151`, `s09_compact.json` `silent_list_truncation`) гейт приёмки не обходит (он уже отработал на полном списке), но compact врёт о составе. Ниже порога отдельной находки.

### F-6 Windows: другой регистр пути = «чужой файл»

- Серьёзность: **major** (на Windows-хосте моста)
- Статус: подтверждено репро
- Где: `grok_delegate/contracts.py:293-311` (сравнение строк без `casefold`), git на Windows репортит индексный регистр
- Что receipt утверждает: `UNEXPECTED_CHANGED_FILES: expected.txt` (затирает `EXPECTED_ARTIFACT_NOT_CHANGED: EXPECTED.TXT`). Хост думает, worker трогал не тот файл.
- Что произошло на самом деле: в baseline был `expected.txt`; task просил `EXPECTED.TXT`; worker записал `NEW\n` в тот же inode. `artifacts=["EXPECTED.TXT"]` (Path на Windows находит файл), содержимое `NEW`. Один файл, два имени.
- Репро: `s05_artifact_paths.json` / `case`.
- Последствие для оператора: отвергнет сделанную работу. Цена ошибки: «никто не пишет EXPECTED.TXT» — хост на Windows часто не следит за регистром в packet.

### F-7 `tests=[]` и `tests_skipped_reason=null` на путях, где verifier не вызывался

- Серьёзность: **minor**
- Статус: подтверждено репро
- Где: `grok_delegate/agent_runtime.py:270-303` (`diff_snapshot_failure` без skip-reason), `823-849` (`_base_receipt` без поля), `contracts.py:272` (`setdefault(..., None)`), схема `tests_skipped_reason`: «null means the verifier ran»
- Что receipt утверждает: пустой `tests` + `null` skip = «verifier гонялся, вот его результаты». Evidence и `AGENTS.md` обещают: пустой `tests` всегда объясняется одним словом.
- Что произошло на самом деле: verifier не вызывался. `DIFF_SNAPSHOT_FAILED`: `status=failed`, `tests=[]`, `tests_skipped_reason=null` (`s08_snapshot_failed.json`, `s10_tests_skip.json` / `diff_snapshot_failed_finalize`). `CANCELLED_BEFORE_START`: то же с `status=cancelled`.
- Репро: `run_scenarios.py` s08 и s10. `_verify_or_explain` сам по себе честный (`NO_CHANGES` / `CANCELLED` / `NOT_A_WRITE_ROLE`).
- Последствие для оператора: не примет job как успех (`ok=false`). Может решить, что тесты гонялись и «нечего было запускать», и не перепроверит. Цена ошибки: смотрит только на `status`/`error_code` — тогда это дыра в документации схемы, не в приёмке.

`completed` + пустые tests execute-роли в `completed` не уезжает: `TEST_EVIDENCE_MISSING` (`s10` / `completed_empty_tests`).

## Проверено и оказалось в порядке

1. **Worker ничего не сделал, артефакт уже лежал.** Статус честный: `no_changes` / `EXECUTE_NO_CHANGES` / `ok=false`, `tests_skipped_reason=NO_CHANGES`, даже если `artifacts` содержит leftover (`s01_stale_artifact.json`; тот же паттерн, что `test_reused_worktree_preexisting_diff_cannot_certify_new_run`). Ложь не в статусе, а в побочном lane-commit — F-1.

2. **Worker удалил tracked-файл.** `changed_files=["expected.txt"]`, `artifacts=[]`, `EXPECTED_ARTIFACT_MISSING`, unified_diff с `deleted file mode`. Untracked leftover — F-1/удаление.

3. **Тест создал unexpected файл.** `UNEXPECTED_CHANGED_FILES: noise.log`. Зачёт. Изготовление **expected** — F-2. Перепись содержимого — следствие F-2, не отдельная ложь статуса.

4. **Тест убит по таймауту.** `passed=false`, `returncode=124`, `status=failed`, `TEST_FAILED`, `tests_skipped_reason=null` (verifier **гонялся**). Не успех. Побочно: `lane_commit.committed=true` даже на красных тестах — хост с `failed` не должен мержить; это не F-успех. (`s04_timeout.json`, `_run_bridge_tests` / `_run_owned_process`: 124 и `timedOut`.)

5. **`expected_artifacts` с `..`.** `GuardError EXPECTED_ARTIFACT_INVALID` на `validate_task_packet`, receipt нет. Каталог / новый nested / регистр — F-4 и F-6. Слэши нормализуются в posix.

6. **Лимит байт `unified_diff`.** На tracked-файле маркер `\n…(truncated)` есть и в полном receipt, и в compact (`s06_tracked.json`, длина 16399 при cap 16384). На новом файле cap не достигается, потому что diff пуст (F-3). Совпадает с `tests/test_grok_delegate.py` (`test_collect_diff_caps_unified_diff_bytes`).

7. **`commits` чужого run.** Не протекают: job 2 noop на той же lane — `commits=[]`, `leaked_first_into_second=false`; job 3 — только свой sha (`s07_commits.json`). Формат первого run — голый SHA lane-commit, не `git log --oneline`, потому что снимок до коммита. Это не ложь состава. `full_changed_files` на reused lane считается от HEAD lane (уже после прошлого job), не от master — это как раз механизм F-1, не отдельный пункт.

8. **`DIFF_SNAPSHOT_FAILED` vs `no_changes`.** Не путается, если читать `status`: `failed` + `error_code=DIFF_SNAPSHOT_FAILED` + `failed_probe=status_porcelain` (`s08_snapshot_failed.json`). `changed_files=[]` у обоих, но статус разный. Дыра только в skip-reason (F-7). Compact `failed_probe` выбрасывает, `error_code`/`blocked_reason` оставляет.

9. **Compact vs полный — поля, которые меняют вывод.** См. F-5. Остальное: `status`/`blocked_reason`/`tests_skipped_reason` поднимаются, если непустые. `state=error` у отвергнутого job (jobs.py:277-279) спасает навигатор, который смотрит `state` раньше `status`.

10. **`tests` и `tests_skipped_reason`.** На write-роли после `_verify_or_explain` согласованы. Противоречие — только ранние выходы (F-7). Пара `completed` + пустые tests закрывается `TEST_EVIDENCE_MISSING`.

## Уже известно (из evidence) — не находки

- `agent-reported` / `exit_code` цепочки (`pytest; echo EXIT_CODE=…`) — не доказательство; в `run_task` в `tests` попадают только `bridge-verifier`.
- Пустой `tests` при `max_turns` / `ACP_STOP_cancelled` — verifier больше не завязан на `completed` (`test_an_out_of_turns_job_still_commits_and_still_reports_its_tests`).
- Тест откатил артефакт → `no_changes`, не stale `completed`.
- `base_ref=HEAD` внутри **одного** job пинится в SHA до worker (`_resolve_base_sha`). Межjob на reused lane база — tip lane, не master (это F-1, не повтор внутриjob-бага).
- `changed_files` и `unified_diff` — разные срезы (схема + скептик). Пустой diff на **новом** файле — сверх того, F-3.
- Leftover unexpected **при непустом** `changed_files` этого run → `UNEXPECTED_CHANGED_FILES` (`test_reused_worktree_preexisting_unexpected_diff_blocks_new_run`). Дыра — пустой `changed_files` (F-1).
- Compact поднимает `blocked_reason` из `result` (не `state=error` + `error=null`). `ok` / `lane_commit` / пустые списки — нет (F-5).
- Preexisting expected не сертифицирует чужой diff (`test_preexisting_expected_artifact_cannot_certify_unrelated_diff`).

## Не запускалось и почему

- Живой Grok CLI / ACP handshake / MCP-сервер из `%LOCALAPPDATA%\grok-mcp` — запрет аудита (worktree моста, это срез самого моста). Адаптер подменялся in-process, как в `tests/test_round8_bridge.py` / `test_lane_commit_and_verification.py`.
- `grok_agent_poll` как JSON-RPC процесс: не вызывался; обёртка `{"ok": True, **compact}` сверена с `server.py:1158` и `compact_job_record` на живом и сконструированном job-конверте.
- `COMMIT_FAILED` через реальный pre-commit hook: на Windows hook мог не исполниться; воспроизведено подменой `commit_lane_work` в импортированном модуле (тот же ранний return, что у hook). Полный `run_task` после этого шёл настоящий.
- Symlink-escape / directory symlinks — evidence: skip на этой машине; вне среза.
- Гонки jobs persistence, permission-гейт, схемы навигатора, качество тестов, установка — вне среза.
- Не вызывались `git checkout` / `stash` / `clean` / `restore` в репозитории Grok CLI. Тестовые git-деревья только под `%TEMP%\grokmcp-receipt\`.
