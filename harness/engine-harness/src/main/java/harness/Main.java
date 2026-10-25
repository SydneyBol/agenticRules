package harness;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.joda.time.DateTimeZone;

import java.io.File;
import java.io.PrintStream;
import java.util.TimeZone;

/**
 * Usage: java -jar target/harness.jar case.json [--show-drl]
 * Prints a single JSON document on stdout. Engine logging (WARN+) goes to stderr.
 */
public final class Main {
    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("usage: java -jar harness.jar <case.json> [--show-drl]");
            System.exit(2);
        }
        relaunchWithShimIfNeeded(args);
        // Production Lambdas run with a UTC default zone; Joda's DateTime.parse() on offset-less strings
        // (e.g. h_bit_date "2026-10-24T12:00:00") uses the JVM default zone, so pin it unless overridden.
        String jvmTz = System.getProperty("harness.jvm.tz", "UTC");
        TimeZone.setDefault(TimeZone.getTimeZone(jvmTz));
        DateTimeZone.setDefault(DateTimeZone.forID(jvmTz));

        ObjectMapper m = new ObjectMapper().enable(SerializationFeature.INDENT_OUTPUT);
        JsonNode cse = m.readTree(new File(args[0]));
        if (args.length > 1 && "--show-drl".equals(args[1])) ((ObjectNode) cse).put("show_drl", true);

        // keep stdout clean for JSON: anything the engine prints via System.out goes to stderr
        PrintStream realOut = System.out;
        System.setOut(System.err);
        ObjectNode result;
        int code = 0;
        try {
            result = Harness.run(cse);
            if (!"ok".equals(result.path("compile").asText())) code = 3;
            else if (result.has("self_test") && !result.get("self_test").path("passed").asBoolean()) code = 6;
        } catch (Throwable t) {
            result = m.createObjectNode();
            result.put("harness_error", t.toString());
            java.io.StringWriter sw = new java.io.StringWriter();
            t.printStackTrace(new java.io.PrintWriter(sw));
            result.put("stack", sw.toString());
            code = 4;
        }
        result.put("jvm_default_timezone", jvmTz);
        realOut.println(m.writeValueAsString(result));
        realOut.flush();
        System.exit(code); // engine may leave non-daemon threads around
    }

    /**
     * MVEL 2.4.14 (inside the engine jar) needs java.lang.Compiler, removed in JDK 21. If it's missing,
     * re-exec this JVM with --patch-module java.base=lib/jdk-compat-shim.jar (built by `mvn package`).
     */
    static void relaunchWithShimIfNeeded(String[] args) throws Exception {
        try {
            Class.forName("java.lang.Compiler");
            return;
        } catch (ClassNotFoundException missing) {
            if (System.getProperty("harness.relaunched") != null) {
                System.err.println("java.lang.Compiler still missing after relaunch; is lib/jdk-compat-shim.jar present?");
                System.exit(5);
            }
        }
        java.nio.file.Path self = java.nio.file.Path.of(Main.class.getProtectionDomain().getCodeSource().getLocation().toURI());
        java.nio.file.Path base = java.nio.file.Files.isDirectory(self) ? self.getParent() : self.getParent(); // target/
        java.nio.file.Path shim = base.resolve("lib").resolve("jdk-compat-shim.jar");
        java.util.List<String> cmd = new java.util.ArrayList<>();
        cmd.add(java.nio.file.Path.of(System.getProperty("java.home"), "bin", "java").toString());
        cmd.add("--patch-module");
        cmd.add("java.base=" + shim);
        cmd.addAll(java.lang.management.ManagementFactory.getRuntimeMXBean().getInputArguments());
        cmd.add("-Dharness.relaunched=true");
        cmd.add("-cp");
        cmd.add(System.getProperty("java.class.path"));
        cmd.add(Main.class.getName());
        cmd.addAll(java.util.Arrays.asList(args));
        Process p = new ProcessBuilder(cmd).inheritIO().start();
        System.exit(p.waitFor());
    }
}
