package harness;

import com.gravty.common.db.helper.DbHelper;
import com.gravty.common.db.tables.Offer;
import com.gravty.common.db.tables.Product;
import com.gravty.common.db.tables.Program;
import com.gravty.common.db.tables.RewardProduct;
import com.gravty.common.environment.EnvironmentProviderImpl;
import com.gravty.common.environment.config.GeneralConfigHelper;
import com.gravty.common.program.DataHelper;
import com.gravty.common.program.LoyaltyAccount;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.util.*;
import java.util.function.Function;

/**
 * Builds the stubbed infrastructure (no AWS / DynamoDB / S3 / Redis / Kinesis) and wires it into the
 * engine's singletons (EnvironmentProviderImpl.ourInstance, S3Helper.ourInstance).
 *
 * Only *data lookups* are stubbed: program policy, feature switches, offer, reward products (SKU attributes),
 * privilege products. Everything that evaluates or mutates state is real engine code.
 */
public final class Infra {

    public final Program program;
    public final Map<String, RewardProduct> rewardProducts = new HashMap<>();
    public final Map<Long, Product> privilegeProducts = new HashMap<>();
    public final List<Map<String, Object>> features = new ArrayList<>();
    public final Map<String, String> config = new HashMap<>();
    public final Set<String> autoCreatedAccounts = new TreeSet<>();
    public Offer offer;

    public final DataHelper dataHelper;
    public final DbHelper dbHelper;
    public final GeneralConfigHelper configHelper;

    public Infra(long programId, String timezone, int decimalScale) {
        this.program = new Program();
        program.setProgramId(programId);
        program.setTimezone(timezone);
        program.setDecimalScale(decimalScale);
        // Accounts referenced by pointsAction but not declared in the case are auto-created with defaults
        // (programPolicy.getAccounts().get(acc) is dereferenced unconditionally in OfferActionHelperImpl).
        program.setAccounts(new HashMap<>() {
            @Override
            public LoyaltyAccount get(Object key) {
                LoyaltyAccount la = super.get(key);
                if (la == null && key != null) {
                    la = new LoyaltyAccount();
                    put(String.valueOf(key), la);
                    autoCreatedAccounts.add(String.valueOf(key));
                }
                return la;
            }
        });

        config.put("region", "us-east-1"); // never used for a network call; S3Helper is pre-seeded below

        Map<String, Function<Object[], Object>> cfg = new HashMap<>();
        cfg.put("getString", a -> config.get(String.valueOf(a[0])));
        cfg.put("getEnvName", a -> "local-harness");
        this.configHelper = Stubs.of(GeneralConfigHelper.class, cfg);

        Map<String, Function<Object[], Object>> db = new HashMap<>();
        // all DB reads return "not found"/empty: member state is supplied in-memory by the harness.
        this.dbHelper = Stubs.of(DbHelper.class, db);

        Map<String, Function<Object[], Object>> dh = new HashMap<>();
        dh.put("getProgramData", a -> program);
        dh.put("getFeatures", a -> features);
        dh.put("getOffer", a -> (offer != null && a[2] != null && ((Long) a[2]) == offer.getOfferID()) ? offer : null);
        dh.put("getRewardProducts", a -> {
            @SuppressWarnings("unchecked") Set<String> skus = (Set<String>) a[1];
            Map<String, RewardProduct> out = new HashMap<>();
            for (String s : skus) if (rewardProducts.containsKey(s)) out.put(s, rewardProducts.get(s));
            return out;
        });
        dh.put("getProduct", a -> privilegeProducts.get((Long) a[1]));
        this.dataHelper = Stubs.of(DataHelper.class, dh);
    }

    public void enableFeature(String toggleName, boolean active) {
        features.removeIf(m -> toggleName.equals(m.get("toggle_name")));
        Map<String, Object> f = new HashMap<>();
        f.put("toggle_name", toggleName);
        f.put("value", new HashMap<>(Map.of("active", active)));
        features.add(f);
    }

    /** Install this infra as the engine's EnvironmentProvider singleton (and pre-seed S3Helper). */
    public EnvironmentProviderImpl install() throws Exception {
        Constructor<EnvironmentProviderImpl> c = EnvironmentProviderImpl.class.getDeclaredConstructor(String.class);
        c.setAccessible(true);
        EnvironmentProviderImpl env = c.newInstance((String) null); // env == null -> init() skipped, no AWS clients
        set(env, "configHelper", configHelper);
        set(env, "dbHelper", dbHelper);
        set(env, "dataHelper", dataHelper);
        Field inst = EnvironmentProviderImpl.class.getDeclaredField("ourInstance");
        inst.setAccessible(true);
        inst.set(null, env);

        // OfferActionHelperImpl's constructor calls S3Helper.getInstance(region), which would build real AWS
        // SDK clients. Pre-seed the singleton with an un-initialised instance so no client is ever created.
        Class<?> s3 = Class.forName("com.gravty.common.aws.S3Helper");
        Field s3inst = s3.getDeclaredField("ourInstance");
        s3inst.setAccessible(true);
        if (s3inst.get(null) == null) s3inst.set(null, allocate(s3));
        return env;
    }

    static void set(Object target, String field, Object value) throws Exception {
        Field f = target.getClass().getDeclaredField(field);
        f.setAccessible(true);
        f.set(target, value);
    }

    static Object allocate(Class<?> cls) throws Exception {
        Field uf = Class.forName("sun.misc.Unsafe").getDeclaredField("theUnsafe");
        uf.setAccessible(true);
        Object unsafe = uf.get(null);
        return unsafe.getClass().getMethod("allocateInstance", Class.class).invoke(unsafe, cls);
    }
}
