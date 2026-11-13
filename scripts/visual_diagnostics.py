"""Bounded diagnostic include integration, after all gameplay generators."""
def once(text,before,after):
    if after in text: return text
    if text.count(before)!=1: raise ValueError('Visual diagnostic anchor changed: '+before)
    return text.replace(before,after,1)
def apply(text):
    text=once(text,'#include "bbsc_peer_display.inc"','#include "bbsc_visual_diagnostics.inc"\n#include "bbsc_peer_display.inc"')
    return once(text,'#include "bbsc_visibility_wire.h"','''#include "bbsc_visibility_wire.h"
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
// Keep USER declarations out: core has its own existing window ABI declarations.
#pragma push_macro("NOUSER")
#pragma push_macro("near")
#pragma push_macro("far")
#ifndef NOUSER
#define NOUSER
#endif
#include <windows.h>
#pragma pop_macro("far")
#pragma pop_macro("near")
#pragma pop_macro("NOUSER")
#endif''')
