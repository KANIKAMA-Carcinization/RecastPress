# RecastPress ��PHP-Wasm hook PoC �f���菇

## �f���{�ԗp�̌Œ萬��run

�ŐV�̐���run��[44488505287](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44488505287)�A�Ώ�commit��`6fc202e7f16d32406477498a903d92f576564301`�ł��B�Œ�PHP-WASIX 8.5.12��WordPress 7.1.3��9�V�i���I�����ׂĐ������Asource inputs�s�ς��m�F���܂����Bartifact��[14512694283](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44488505287/artifacts/14512694283)�ASHA-256��`d972b64004e34df9395285570030214b9c3c2240f995f11d559ff6cfa1c9fd0b`�ŁA2026-12-03 09:05 UTC�܂Ŏ擾�ł��܂��B

�O��̃f���ł�run��`php-wasix` job��artifact `v06-wp-hook-dispatch-44429302930-1`���J���A`latest.json`��`action-deferred-add`��\�����܂��B1��ڂ̊ϑ�����`all -> mutator -> peer -> tail`�A2��ڂ�`all -> mutator -> peer -> added -> tail`�ł��Bnative oracle��v�A`did_action()`��1����2�ւ̑����A�ŏIcallback table�Ahook state reset�Alive handles 0���m�F�ł��܂��B�ؐՂ�[����receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-overlarge-accepted-args-run-44429302930.json)�ɋL�^���Ă��܂��Bartifact��SHA-256��`53214c508659f583276ee4a68f91fdacf11e50bec08cd9b3af2d8ef8ead4d55f`�ŁA2026-12-03 00:43 UTC�܂Ŏ擾�ł��܂��B

����PHP���N�G�X�g����2��`do_action()`�ƁA�D��x5�Œǉ�����callback��2��ڂɎ��s����鋓����run 44429302930�Ō��؍ς݂ł��B�ߋ��̌Œ萬��run��[44354882884](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44354882884)��[source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-deferred-add-run-44354882884.json)�Ɏc���Ă��܂��B

����PoC��GitHub Actions��Ubuntu runner��œ��삷�錟�؃f���ł��BWindows��Mac���`check.py`�𒼐ڎ��s����菇�ł͂���܂���B������Mac Docker�f���͕ʂ̃f���o�H�ł��B

## �f���o�H

����PoC�̎��s����Ubuntu 24.04��GitHub Actions�ł��BWindows�̊J�����ł�`check.py`�͎��s�ł��܂���B������[Mac Docker�f���菇](local-demo-macos.ja.md)�͐ÓI�y�[�W�\���p�ŁA����PHP-Wasm hook PoC�Ƃ͕ʂ̃f���ł��B

Latest validated run: [44488505287](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44488505287), source commit `6fc202e7f16d32406477498a903d92f576564301`. The pinned PHP-WASIX 8.5.12 and WordPress 7.1.3 workflow passed all nine scenarios with unchanged source inputs. In `action-ref-array`, the priority-5 callback adds a priority-7 callback during dispatch. The new callback receives the same two reference cells after the first mutations, changes both, and priority 10 observes those changes; the priority-2 `accepted_args=0` callback still receives no arguments. The native oracle matched, the action result stayed null, and cleanup released the argument frame with zero live handles. See the [source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-ref-array-runtime-add-run-44488505287.json). Current artifact [14512694283](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44488505287/artifacts/14512694283), digest `d972b64004e34df9395285570030214b9c3c2240f995f11d559ff6cfa1c9fd0b`, expires 2026-12-03 09:05 UTC.

## ����run��artifact���J��

1. Open [successful run 44488505287](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44488505287) and confirm the workflow and `php-wasix` job succeeded.
2. Download [artifact 14512694283](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44488505287/artifacts/14512694283), named `v06-wp-hook-dispatch-44488505287-1`.
3. In `latest.json`, show all nine exit codes and `run.result.action-ref-array`. Highlight the callback order `first -> added (priority 7) -> second (priority 10)`, the values passed through both reference cells, the priority-2 zero-argument callback, native oracle match, null action result, released argument frame, and zero live handles. The artifact ZIP SHA-256 is `d972b64004e34df9395285570030214b9c3c2240f995f11d559ff6cfa1c9fd0b`.

����workflow�͌���default branch��`main`�ɂ͂Ȃ��A`workflow_dispatch`�ɂ��Actions��ʂ���̎蓮�N���͂ł��܂���B`dev/v0.6`��̑Ώۃt�@�C���ύX���܂�push�Ŏ������s����܂��Brun��artifact����`v06-wp-hook-dispatch-<run-id>-<run-attempt>`�ł��B

## �ŐV���s�̈���

run [44425880569](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44425880569)��checker��JSON�Q�ƊK�w���Ŏ��s���܂����B��PHP-WASM runner���̂�9�V�i���I���ׂ�exit 0�ŁA`accepted_args=5`��callback��`["left", "right"]`���󂯎���Ă��܂����B��r�ӏ��𒼂���[run 44429302930](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44429302930)�͑S�V�i���I�������Anative oracle��v��source inputs�s�ς��m�F���܂����B�����ƍŏ���artifact digest��[�f�freceipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-overlarge-accepted-args-run-44425880569.json)�A�C���ł̏ؐՂ�[����receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-overlarge-accepted-args-run-44429302930.json)�ɋL�^���Ă��܂��B�����̔��\�ł�run `44488505287` artifact `14512694283`���g���܂��B

## ���\���Ɍ�����ϑ�

�����̃f���́A`latest.json`��`run.result.action-deferred-add`���J���A���������܂��B

- 1��ڂ�callback����`all -> mutator -> peer -> tail`�ŁA�D��x5�Œǉ�����callback�͂܂����s����Ȃ�
- 2��ڂ�`all -> mutator -> peer -> added -> tail`�B����PHP request���ɒx��callback�����s�����
- `did_action()`��1����2�֐i�݁Anative WordPress oracle����v���Acallback table��4�s�Ahook state reset�A�����live handle����0
- `run.exit_codes`��9�V�i���I�����ׂ�0

�\���Ƃ��ē���artifact��`run.result.action`��\�����AWordPress��`all` callback�A�ʏ�callback���Aaccepted args�Aaction counter��cleanup������ł��܂��B`native_oracle_accepted_args_match: true`��`accepted_args_overlarge: ["left", "right"]`��native parity�̊m�F�ӏ��ł��B

�ǉ��̋Z�p�������K�v�ȏꍇ�����A`latest.json`��`run.result.action-ref-array`���J���A���������܂��B

- `status`: `passed`�A`native_oracle_contract_match`: `true`
- callback `first`��`seed`��`left`���󂯎��A�Q�ƒl������������
- callback `second`��`value-mutated-by-first`��`extra-mutated-by-first`���󂯎��A2�ڂ�����ɏ���������
- �Ăяo�����̍ŏI�l��`value-mutated-by-first`��`extra-mutated-by-second`
- action�̖߂�l��`null`�B�I�����`current_action`�A`doing_action`�A`wp_hook_doing_action`�A`current_priority`��false�A`action_count`��1�A������live handle����0
- All nine `run.exit_codes` entries (`filter`, `ref-array`, `action`, `action-ref-array`, `action-throwable`, `action-nested`, `action-mutation`, `action-removal`, `action-deferred-add`) are 0.

The latest successful run records the ref-array PHP/Rust frame exchange, future-priority addition, active callback removal, and the same-priority callback's invocation on the second pass. `run-*.log` contains the full JSON observations, bridge frames, and source commit.

## Throwable�V�i���I

`latest.json`��`run.result.action-throwable`�ł́A`RuntimeException`�̓���PHP object��Rust���E����������callback�ʒu�ō�throw����A�O����request���E�ŕߑ����ꂽ���Ƃ������܂��B`native_oracle_contract_match`��`true`�A`same_throwable`��`true`�Acallback lease�������2�Aregistry close�O���live handle����0�ł��Bthrow��WP��dispatch loop��cleanup�����f����邽�߁A�㑱callback�͎��s���ꂸ�A`current_action`/`current_filter`��`doing_action`�A`$wp_current_filter`�AWP_Hook��nesting/iteration/current-priority state���c��܂��B����͗�O������Ԃ��񕜂⓯��worker�̍ė��p���������̂ł͂���܂���B

## ����qaction�V�i���I

`latest.json`��`run.result.action-nested`�ł́A`outer_parent_enter`��������`do_action()`���ĂсA`inner_callback`������ɐecallback���ĊJ���A`outer_tail`�֐i�ޏ����������܂��B`native_oracle_contract_match`��`true`�ŁA���S�ȃC�x���g�Ehook stack�Epriority�EWordPress����state���Œ�WordPress 7.1.3��native oracle�Əƍ����Ă��܂��B�O���E������action counter�͊e1�A�qaction�I����͐estack��priority 5�֖߂�A�ŏIstack�͋�Aregistry close����live handle����0�ł��B�����1�i�̓���q�����������A����ɐ[���ē����callback table�̕ύX�͑ΏۊO�ł��B

## �f���͈̔�

This is bounded execution evidence for fixed WordPress fixtures. It covers ordinary action `accepted_args=5` truncation to the two supplied arguments, reference-array filter and action `accepted_args=1` truncation to one argument while preserving later reference mutations, a reference-array filter callback with `accepted_args=0` whose return becomes the next callback's accumulator, and a reference-array action callback with `accepted_args=0` whose declared return does not replace the observed null action result while later reference mutations still occur. Arbitrary PHP values, deeper nesting, Throwable recovery or worker reuse, and production bootstrap integration also remain outside this demo. It does not establish full WordPress compatibility or production readiness.

The reference-array truncation case passed in [real PHP-WASIX run 44459199659](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44459199659). Use its [artifact 14499947334](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44459199659/artifacts/14499947334); the source-bound [receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-ref-array-accepted-args-run-44459199659.json) records the native oracle match and observed arguments.

The corresponding `do_action_ref_array()` truncation case passed in [real PHP-WASIX run 44466526546](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44466526546). Its [artifact 14503086916](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44466526546/artifacts/14503086916) and [source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-ref-array-accepted-args-run-44466526546.json) record the native oracle match, one received argument, and preserved reference mutations.

The `accepted_args=0` `do_action_ref_array()` case passed in [real PHP-WASIX run 44476556483](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44476556483). Its [artifact 14508021951](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44476556483/artifacts/14508021951) and [source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-action-ref-array-zero-accepted-args-run-44476556483.json) record the empty callback argument list, null action result, native oracle match, and preserved later reference mutations.

The `accepted_args=0` filter case passed in [real PHP-WASIX run 44473413660](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44473413660). Its [artifact 14506291461](https://github.com/KANIKAMA-Carcinization/RecastPress/actions/runs/44473413660/artifacts/14506291461) and [source-bound receipt](../experiments/php-wasix-wp-hook-dispatch-probe/evidence/real-php-wasix-ref-array-zero-accepted-args-run-44473413660.json) record the empty argument list and the returned `zero-result` observed by the next callback.
