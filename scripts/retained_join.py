"""Scope same-map reload join lines to actual retained member reinserts."""
def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("Unexpected retained-join source marker: " + before[:90])
    return text.replace(before, after, 1)
def apply(text):
    if '#include "bbsc_retained_join.inc"' not in text:
        text = once(text, 'void ArmBbscReinsertV8() {',
            '#include "bbsc_retained_join.inc"\n\nvoid ArmBbscReinsertV8() {\n'
            '    CaptureBbscRetainedJoinMembers();')
        start = text.index('void TickBbscReinsertV8() {')
        end = text.index('\n// Self-heal (host and guest)', start)
        function = text[start:end]
        function = once(function, '        set_state(task, 4);',
            '        QueueBbscRetainedJoinLine(task);\n        set_state(task, 4);')
        text = text[:start] + function + text[end:]
    if '        ObserveBbscRetainedHostReload(registers->r13);' not in text:
        marker = '    if (site.offset == 0x01938F65) { // Warp.RespawnPoint.Resolve: a load picks its spawn point\n'
        text = once(text, marker, marker + '        ObserveBbscRetainedHostReload(registers->r13);\n')
    return text
