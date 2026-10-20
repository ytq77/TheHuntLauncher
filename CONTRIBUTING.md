# Contributing

Keep changes small and describe the failure they address. Record BBport, BBSC,
game and toolchain versions when reporting compatibility problems.

Run Python tests and native scanner tests for affected behavior. A successful
build is not evidence of in-game correctness: report actual game tests separately.

Do not commit profiles, credentials, saved games, captured packets, crash dumps,
game binaries, full installed packages or personal server addresses. Use the
clean packaging script instead of zipping a played installation.

Keep upstream attribution and SPDX notices. Source revisions are deliberately
locked; an upgrade should include a review of hook bytes, initialization and
co-op behavior.
