// Function map: name, address, callers, callees and every string the function references.
// analyzeHeadless ... -postScript FuncMap.java <out.tsv>
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.address.*;
import java.io.*;
import java.util.*;

public class FuncMap extends GhidraScript {
  String strAt(Address a) {
    Data d = getDataAt(a);
    if (d != null && d.getValue() instanceof String) return (String) d.getValue();
    if (d != null && d.isPointer() && d.getValue() instanceof Address) {
      Data d2 = getDataAt((Address) d.getValue());
      if (d2 != null && d2.getValue() instanceof String) return (String) d2.getValue();
    }
    return null;
  }
  public void run() throws Exception {
    PrintWriter out = new PrintWriter(new OutputStreamWriter(new FileOutputStream(getScriptArgs()[0]), "UTF-8"));
    Listing L = currentProgram.getListing();
    for (Function f : L.getFunctions(true)) {
      List<String> strs = new ArrayList<>();
      Set<String> callees = new LinkedHashSet<>();
      for (Instruction ins : L.getInstructions(f.getBody(), true)) {
        for (Reference r : ins.getReferencesFrom()) {
          if (r.getReferenceType().isCall()) {
            Function c = getFunctionAt(r.getToAddress());
            if (c != null) callees.add(c.getName(true));
          } else {
            String s = strAt(r.getToAddress());
            if (s != null) strs.add(s.replace("\r", "\\r").replace("\n", "\\n").replace("\t", " "));
          }
        }
      }
      Set<String> callers = new LinkedHashSet<>();
      for (Function c : f.getCallingFunctions(monitor)) callers.add(c.getName(true));
      out.println(f.getName(true) + "\t" + f.getEntryPoint() + "\tcallers=" + callers + "\tcallees=" + callees + "\tstrings=" + strs);
    }
    out.close();
  }
}
