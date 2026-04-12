// Ghidra headless post-analysis script: export decompiled C
// @category Analysis
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import java.io.*;

public class GhidraDecompile extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        String outPath;
        if (args.length > 0) {
            outPath = args[0];
        } else {
            outPath = System.getProperty("user.dir") + "/decompiled.c";
        }
        new File(outPath).getParentFile().mkdirs();

        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);

        FunctionIterator funcs = currentProgram.getFunctionManager().getFunctions(true);
        int count = 0;
        PrintWriter pw = new PrintWriter(new FileWriter(outPath));
        while (funcs.hasNext()) {
            Function func = funcs.next();
            DecompileResults results = decomp.decompileFunction(func, 30, monitor);
            if (results != null && results.decompileCompleted()) {
                String code = results.getDecompiledFunction().getC();
                pw.println("// Function: " + func.getName() + " @ " + func.getEntryPoint());
                pw.println(code);
                pw.println();
                count++;
            }
        }
        pw.close();
        decomp.dispose();
        println("Decompiled " + count + " functions to " + outPath);
    }
}
