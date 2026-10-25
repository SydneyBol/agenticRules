package java.lang;

/**
 * JDK-compat shim. java.lang.Compiler was removed in JDK 21, but MVEL 2.4.14 (bundled in the engine fat
 * jar, used by Drools 7.73) references it in a static initializer (AbstractParser.LITERALS). The engine
 * runs on Java 17 in production where the class exists; on newer JDKs the harness patches it back into
 * java.base via --patch-module. All methods are the JDK 17 no-op equivalents.
 */
public final class Compiler {
    private Compiler() {}
    public static boolean compileClass(Class<?> clazz) { return false; }
    public static boolean compileClasses(String string) { return false; }
    public static Object command(Object any) { return null; }
    public static void enable() {}
    public static void disable() {}
}
