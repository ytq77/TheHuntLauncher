// SPDX-License-Identifier: GPL-2.0-or-later
using System;
using System.Collections.Generic;
using System.Linq;

// Session state is scoped to this launcher's fixed user directory; no disk cache or inferred unlocks.
public sealed class TrophySession
{
    public Dictionary<string,object> Snapshot { get; private set; }
    public string Warning { get; private set; }
    public int[] Added { get; private set; }
    HashSet<int> baseline;
    bool awaitingExit;
    public TrophySession() { Added = new int[0]; }
    public static HashSet<int> Earned(Dictionary<string,object> snapshot) {
        var result = new HashSet<int>();
        if (snapshot != null) foreach (Dictionary<string,object> row in (System.Collections.IEnumerable)snapshot["trophies"])
            if (row["unlocked"] != null) result.Add(Convert.ToInt32(row["id"]));
        return result;
    }
    public void Accept(Dictionary<string,object> value) { Snapshot=value; Warning=null; if(awaitingExit) Finish(); else Added=Added.Where(Earned(value).Contains).ToArray(); }
    public void Failed(string error) { Warning=error; }
    public void Begin(bool fresh) {
        bool missing=Snapshot==null || (Snapshot.ContainsKey("missing") && Convert.ToBoolean(Snapshot["missing"]));
        baseline=fresh && !missing ? Earned(Snapshot) : null; Added=new int[0]; awaitingExit=false;
    }
    public void Exited() { awaitingExit=true; }
    void Finish() { Added=baseline == null ? new int[0] : Earned(Snapshot).Except(baseline).OrderBy(i=>i).ToArray(); awaitingExit=false; }
}
