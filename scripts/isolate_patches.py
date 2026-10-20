"""Exclude the normal install's built-in XML when engine and data live separately."""
def patched_script(text):
    before = "        if path.resolve()==Path(exclude).resolve(): continue  # the built-in file (patches/ in a checkout)"
    after = "        if path.resolve() in {Path(exclude).resolve(), (directory/'Bloodborne.xml').resolve()}: continue  # built-in XML is not a third-party patch"
    if text.count(before) != 1:
        raise ValueError("Unexpected patches.py version; refusing an unverified change.")
    return text.replace(before, after)
