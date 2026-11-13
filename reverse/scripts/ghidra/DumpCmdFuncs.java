// Decompiles every function that references a string matching a regex (e.g. the commands "*?..").
// analyzeHeadless ... -postScript DumpCmdFuncs.java <out.c> "<regex>"
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.io.*;
import java.util.*;
import java.util.regex.*;

public class DumpCmdFuncs extends GhidraScript {
  public void run() throws Exception {
    PrintWriter out = new PrintWriter(new OutputStreamWriter(new FileOutputStream(getScriptArgs()[0]), "UTF-8"));
    Pattern p = Pattern.compile(getScriptArgs()[1]);
    DecompInterface di = new DecompInterface();
    di.openProgram(currentProgram);
    Map<Function, Set<String>> why = new LinkedHashMap<>();
    DataIterator it = currentProgram.getListing().getDefinedData(true);
    while (it.hasNext()) {
      Data d = it.next();
      if (!(d.getValue() instanceof String)) continue;
      String s = (String) d.getValue();
      if (!p.matcher(s).find()) continue;
      for (Reference r : getReferencesTo(d.getAddress())) {
        Function f = getFunctionContaining(r.getFromAddress());
        if (f != null) why.computeIfAbsent(f, k -> new TreeSet<>()).add(s);
      }
    }
    out.println("// functions: " + why.size());
    for (Map.Entry<Function, Set<String>> e : why.entrySet()) {
      DecompileResults r = di.decompileFunction(e.getKey(), 30, monitor);
      out.println("\n// ===== " + e.getKey().getName(true) + " @ " + e.getKey().getEntryPoint() + " strings=" + e.getValue());
      out.println(r.decompileCompleted() ? r.getDecompiledFunction().getC() : "// not decompiled (timeout)");
      out.flush();
    }
    out.close();
  }
}
