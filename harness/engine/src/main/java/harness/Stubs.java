package harness;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.*;
import java.util.function.Function;

/**
 * Tiny dependency-free stubbing helper based on java.lang.reflect.Proxy.
 * (Avoids Mockito/ByteBuddy, whose bundled versions in the engine fat jar do not support JDK 25.)
 *
 * Unhandled methods return a "safe empty" value (null for objects, empty collections, 0, false)
 * and are recorded so the harness can report which infrastructure calls the engine made.
 */
public final class Stubs {
    private Stubs() {}

    /** Every unhandled infrastructure call, in order (Interface.method). */
    public static final Set<String> UNHANDLED_CALLS = Collections.synchronizedSet(new LinkedHashSet<>());

    @SuppressWarnings("unchecked")
    public static <T> T of(Class<T> iface, Map<String, Function<Object[], Object>> handlers) {
        InvocationHandler h = (proxy, method, args) -> {
            String name = method.getName();
            if (method.getDeclaringClass() == Object.class) {
                switch (name) {
                    case "toString": return "Stub<" + iface.getSimpleName() + ">";
                    case "hashCode": return System.identityHashCode(proxy);
                    case "equals": return proxy == args[0];
                    default: return null;
                }
            }
            Function<Object[], Object> fn = handlers.get(name + "/" + method.getParameterCount());
            if (fn == null) fn = handlers.get(name);
            if (fn != null) return fn.apply(args == null ? new Object[0] : args);
            if (method.isDefault()) {
                return InvocationHandler.invokeDefault(proxy, method, args);
            }
            UNHANDLED_CALLS.add(iface.getSimpleName() + "." + name + "/" + method.getParameterCount());
            return defaultValue(method);
        };
        return (T) Proxy.newProxyInstance(iface.getClassLoader(), new Class<?>[]{iface}, h);
    }

    static Object defaultValue(Method m) {
        Class<?> r = m.getReturnType();
        if (r == void.class) return null;
        if (r == boolean.class) return false;
        if (r == int.class) return 0;
        if (r == long.class) return 0L;
        if (r == double.class) return 0d;
        if (r == float.class) return 0f;
        if (r == short.class) return (short) 0;
        if (r == byte.class) return (byte) 0;
        if (r == char.class) return (char) 0;
        if (r == List.class || r == Collection.class || r == Iterable.class) return new ArrayList<>();
        if (r == Set.class) return new HashSet<>();
        if (r == Map.class) return new HashMap<>();
        if (r == Optional.class) return Optional.empty();
        return null;
    }
}
