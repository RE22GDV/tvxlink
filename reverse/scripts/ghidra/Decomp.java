// Decompiles the functions that contain the given addresses (comma-separated).
// analyzeHeadless ... -postScript Decomp.java <out.c> 0x41de40,0x4a1cd0,...
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.*;
import java.io.*;
import java.util.*;

public class Decomp extends GhidraScript {
  public void run() throws Exception {
    String[] a = getScriptArgs();
    PrintWriter out = new PrintWriter(new OutputStreamWriter(new FileOutputStream(a[0]), "UTF-8"));
    DecompInterface di = new DecompInterface();
    di.openProgram(currentProgram);
    Set<Function> done = new LinkedHashSet<>();
    for (String x : a[1].split(",")) {
      Function f = getFunctionContaining(toAddr(x.trim()));
      if (f == null) { out.println("// no function at address " + x); continue; }
      if (!done.add(f)) continue;
      DecompileResults r = di.decompileFunction(f, 120, monitor);
      out.println("\n// ===== " + f.getName(true) + " @ " + f.getEntryPoint() + "  (requested " + x.trim() + ")");
      out.println(r.decompileCompleted() ? r.getDecompiledFunction().getC() : "// not decompiled");
      out.flush();
    }
    out.close();
  }
}
