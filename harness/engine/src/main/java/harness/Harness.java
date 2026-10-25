package harness;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.gravty.bit.BitExecutionContext;
import com.gravty.bit.BitExecutionContextImpl;
import com.gravty.bit.input.BitInput;
import com.gravty.bitprocessor.PhaseExecutionStatus;
import com.gravty.common.db.tables.*;
import com.gravty.common.enums.BitCategory;
import com.gravty.common.enums.MemberOfferType;
import com.gravty.common.enums.OfferLevelType;
import com.gravty.common.offer.template_drl.DRLDecoderFactory;
import com.gravty.common.points.PointsManagerImpl;
import com.gravty.common.program.BaseKieUtilities;
import com.gravty.common.program.LoyaltyAccount;
import com.gravty.common.template_engine.TemplateEngine;
import com.gravty.common.utilities.KieUtilities;
import com.gravty.common.utilities.MemberOfferUtils;
import com.gravty.common.utilities.Utilities;
import com.gravty.helpers.bitextractor.BitExtractorImpl;
import com.gravty.helpers.rules.RulesExecutionHelper;
import com.gravty.member.EngineMember;
import com.gravty.member.EngineMemberImpl;
import com.gravty.rules.action.helper.OfferEligibilityHelperImpl;
import com.gravty.rules.kb.GravtyRuleBase;
import org.apache.commons.codec.binary.Base64;
import org.kie.api.KieBase;
import org.kie.api.KieServices;
import org.kie.api.builder.KieBuilder;
import org.kie.api.builder.KieFileSystem;
import org.kie.api.builder.Message;
import org.kie.api.event.rule.AfterMatchFiredEvent;
import org.kie.api.event.rule.DefaultAgendaEventListener;
import org.kie.api.runtime.StatelessKieSession;

import java.io.*;
import java.lang.reflect.Field;
import java.lang.reflect.Proxy;
import java.math.BigDecimal;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Runs one case: render (creation stage) -> base64 -> real DRLDecoderFactory chain -> DROOLS_DRL_HEADER +
 * KieUtilities.generateKie -> for each BIT: real BitExecutionContextImpl + EngineMemberImpl + StatelessDroolsExecution.
 */
public final class Harness {

    static final ObjectMapper M = new ObjectMapper();
    static final Pattern LEFTOVER = Pattern.compile("\\$\\{[^}]*}");

    public static ObjectNode run(JsonNode cse) throws Exception {
        ObjectNode out = M.createObjectNode();
        ArrayNode warnings = out.putArray("warnings");

        long offerId = cse.path("offer_id").asLong(123);
        int priority = cse.path("priority").asInt(1);
        boolean nonCumulative = cse.path("non_cumulative").asBoolean(false);
        String offerLevel = cse.path("offer_level").asText(nonCumulative ? "header_level" : "");
        JsonNode prog = cse.path("program");
        long programId = prog.path("id").asLong(cse.path("bits").path(0).path("header").path("h_program_id").asLong(1));
        String tz = prog.path("timezone").asText("UTC");
        int scale = prog.path("decimal_scale").asInt(2);

        // ---------------- infra ----------------
        Infra infra = new Infra(programId, tz, scale);
        infra.enableFeature("SkuAttributesLoading", true); // so product attributes from case.products merge into lines
        prog.path("features").fields().forEachRemaining(e -> infra.enableFeature(e.getKey(), e.getValue().asBoolean()));
        prog.path("accounts").fields().forEachRemaining(e -> {
            LoyaltyAccount la = new LoyaltyAccount();
            if (e.getValue().has("account_type")) la.setAccountType(e.getValue().get("account_type").asText());
            infra.program.getAccounts().put(e.getKey(), la);
        });
        cse.path("products").fields().forEachRemaining(e -> {
            RewardProduct rp = new RewardProduct();
            rp.setSkuID(e.getKey());
            Map<String, String> attrs = new HashMap<>();
            e.getValue().fields().forEachRemaining(a -> attrs.put(a.getKey(), a.getValue().isTextual() ? a.getValue().asText() : a.getValue().toString()));
            rp.setAttributes(attrs);
            infra.rewardProducts.put(e.getKey(), rp);
        });
        cse.path("privilege_products").fields().forEachRemaining(e -> {
            Product p = new Product();
            p.setProductID(Long.parseLong(e.getKey()));
            p.setCost(e.getValue().path("cost").asDouble(0));
            p.setName(e.getValue().path("name").asText("product-" + e.getKey()));
            p.setSponsorID(e.getValue().path("sponsor_id").asLong(cse.path("bits").path(0).path("header").path("h_sponsor_id").asLong(1)));
            infra.privilegeProducts.put(p.getProductID(), p);
        });
        infra.install();

        // ---------------- DRL: creation-stage render ----------------
        String rawDrl = cse.path("drl").asText();
        if (cse.has("drl_file")) rawDrl = new String(java.nio.file.Files.readAllBytes(java.nio.file.Path.of(cse.get("drl_file").asText())));
        String stored;
        JsonNode params = cse.path("params");
        String mode = cse.path("param_substitution").asText("freemarker");
        try {
            stored = renderCreationStage(rawDrl, offerId, params, mode);
        } catch (Exception e) {
            out.put("compile", "error");
            out.put("stage", "template_render");
            out.putArray("messages").add(String.valueOf(e.getMessage()));
            return out;
        }

        // ---------------- DRL: runtime decoder chain (real) ----------------
        Offer offer = new Offer();
        offer.setOfferID(offerId);
        offer.setSponsorID(cse.path("bits").path(0).path("header").path("h_sponsor_id").asLong(1));
        offer.setPriority(priority);
        offer.setNonCumulative(nonCumulative);
        if (!offerLevel.isEmpty()) offer.setOfferLevelType(offerLevel);
        if (cse.has("targeted_segment")) offer.setTargetedSegment(cse.get("targeted_segment").asText());
        offer.setDrl(new String(Base64.encodeBase64(stored.getBytes())));
        infra.offer = offer;

        String decoded = DRLDecoderFactory.get().decode(offer);
        Matcher lm = LEFTOVER.matcher(decoded);
        Set<String> leftovers = new TreeSet<>();
        while (lm.find()) leftovers.add(lm.group());
        if (!leftovers.isEmpty()) {
            warnings.add("Decoder chain left unresolved placeholders " + leftovers
                    + " (Freemarker failed inside EligibilityDecorator/ParamDecorator; the engine logs and passes the DRL through undecoded).");
        }
        String fullDrl = BaseKieUtilities.DROOLS_DRL_HEADER + decoded + '\n';
        if (cse.path("show_drl").asBoolean(false)) out.put("decoded_drl", decoded);

        // ---------------- compile (real) ----------------
        KieBase kieBase;
        try {
            kieBase = KieUtilities.generateKie(fullDrl, "harness-" + offerId);
        } catch (RuntimeException e) {
            out.put("compile", "error");
            out.put("stage", "drools");
            out.put("engine_error", String.valueOf(e.getMessage()));
            ArrayNode msgs = out.putArray("messages");
            for (String m : kieMessages(fullDrl, offerId)) msgs.add(m);
            int headerLines = BaseKieUtilities.DROOLS_DRL_HEADER.split("\n", -1).length - 1;
            out.put("header_line_offset", headerLines);
            out.put("decoded_drl", decoded);
            return out;
        }
        if (kieBase == null) { // KieUtilities.generateKie swallows some errors and returns null
            out.put("compile", "error");
            out.put("stage", "drools");
            ArrayNode msgs = out.putArray("messages");
            for (String m : kieMessages(fullDrl, offerId)) msgs.add(m);
            out.put("decoded_drl", decoded);
            return out;
        }
        out.put("compile", "ok");
        ArrayNode ruleNames = out.putArray("rules_compiled");
        kieBase.getKiePackages().forEach(p -> p.getRules().forEach(r -> ruleNames.add(r.getName())));

        // ---------------- member state (carried across BITs) ----------------
        JsonNode mj = cse.path("member");
        Member member = new Member();
        String memberId = mj.path("id").asText(cse.path("bits").path(0).path("header").path("h_member_id").asText("M1"));
        member.setMemberId(memberId);
        Map<String, String> attrs = new HashMap<>();
        mj.path("attributes").fields().forEachRemaining(a -> attrs.put(a.getKey(), a.getValue().isTextual() ? a.getValue().asText() : a.getValue().toString()));
        if (mj.has("tier") && !attrs.containsKey("tier")) attrs.put("tier", mj.get("tier").asText());
        member.setAttributes(attrs);
        Map<String, BigDecimal> bal = new HashMap<>();
        mj.path("balances").fields().forEachRemaining(b -> bal.put(b.getKey(), new BigDecimal(b.getValue().asText())));
        member.setLoyaltyBalances(bal);
        Map<Long, MemberOffer> memberOffers = new HashMap<>();
        cse.path("member_offers").fields().forEachRemaining(e -> {
            MemberOffer mo = new MemberOffer(memberId, Long.parseLong(e.getKey()));
            e.getValue().fields().forEachRemaining(kv -> mo.putMto(kv.getKey(), kv.getValue().asText()));
            memberOffers.put(mo.getOfferID(), mo);
        });

        // ---------------- segment (optional): the engine's own segment executor, as offer simulation runs it ----------------
        // "segment_drl": the DRL the UI's segment canvas wrote; "segment_id": the id its rule adds. A member outside the
        // segment gets nothing from this offer (EligibileOfferStrategyImpl.filterOfferByTargetType drops the offer).
        boolean inSegment = true;
        if (cse.has("segment_drl")) {
            SegmentDefinition sd = new SegmentDefinition();
            sd.setSegmentId(cse.path("segment_id").asText());
            sd.setDynamicSegmentation(true);
            sd.setCriteriaDrl(new String(Base64.encodeBase64(cse.get("segment_drl").asText().getBytes())));
            ObjectNode so = out.putObject("segment");
            try {
                KieBase segKb = BaseKieUtilities.generateDynamicSegmentsKieBase(programId, List.of(sd));
                com.gravty.segment.MemberSegmentExecutionContextImpl sctx = new com.gravty.segment.MemberSegmentExecutionContextImpl(
                        member, memberOffers, programId, null,
                        new com.gravty.common.cobrandcard.loader.MemberAccountCardLoaderImpl(new ArrayList<>(), new HashMap<>()), new HashMap<>());
                com.gravty.rules.SegmentDroolsExecutor.getInstance().fireAllRules(segKb, sctx);
                Set<String> ids = sctx.getValidSegmentIds();
                inSegment = ids.contains(sd.getSegmentId());
                ArrayNode idsOut = so.putArray("valid_segment_ids");
                ids.forEach(idsOut::add);
                so.put("member_in_segment", inSegment);
            } catch (Exception e) {
                so.put("error", String.valueOf(e));
                inSegment = false;
            }
        }

        // ---------------- BITs ----------------
        ArrayNode bitsOut = out.putArray("bits");
        int idx = 0;
        for (JsonNode b : cse.path("bits")) {
            if (!inSegment) {  // offer not eligible for this member: no rule runs, nothing awarded
                ObjectNode skipped = bitsOut.addObject();
                skipped.put("status", "SUCCESS");
                skipped.put("skipped", "member not in targeted segment");
                skipped.putArray("points");
                skipped.putArray("privileges");
                idx++;
                continue;
            }
            bitsOut.add(runBit(idx++, b, kieBase, offer, infra, member, memberOffers));
        }
        if (!infra.autoCreatedAccounts.isEmpty())
            warnings.add("Loyalty accounts auto-created with default settings: " + infra.autoCreatedAccounts);
        ArrayNode unhandled = out.putArray("stubbed_infra_calls");
        synchronized (Stubs.UNHANDLED_CALLS) { Stubs.UNHANDLED_CALLS.forEach(unhandled::add); }
        if (cse.has("expect")) out.set("self_test", checkExpectations(cse.get("expect"), bitsOut, String.valueOf(offerId)));
        return out;
    }

    /**
     * Optional "expect": [ per-BIT { "status", "points":[{action,account,tags,total}] (exact set),
     * "privileges":[product_id...] (exact list), "mto":{name:value} (subset of this offer's MTO map),
     * "mta":{name:value} (subset), "rules_fired":[...] (exact) } ].
     */
    static ObjectNode checkExpectations(JsonNode expect, ArrayNode bits, String offerId) {
        ObjectNode res = M.createObjectNode();
        ArrayNode fails = M.createArrayNode();
        for (int i = 0; i < expect.size(); i++) {
            JsonNode e = expect.get(i);
            JsonNode b = bits.get(i);
            String p = "bit[" + i + "] ";
            if (b == null) { fails.add(p + "missing"); continue; }
            String status = e.path("status").asText("SUCCESS");
            if (!status.equals(b.path("status").asText())) fails.add(p + "status " + b.path("status").asText() + " != " + status);
            if (e.has("points")) {
                Set<String> want = new TreeSet<>(), got = new TreeSet<>();
                e.get("points").forEach(x -> want.add(x.path("action").asText() + "|" + x.path("account").asText() + "|" + x.path("tags").asText() + "|" + x.path("total").asDouble()));
                b.path("points").forEach(x -> got.add(x.path("action").asText() + "|" + x.path("account").asText() + "|" + x.path("tags").asText() + "|" + x.path("total").asDouble()));
                if (!want.equals(got)) fails.add(p + "points " + got + " != " + want);
            }
            if (e.has("privileges")) {
                List<String> want = new ArrayList<>(), got = new ArrayList<>();
                e.get("privileges").forEach(x -> want.add(x.asText()));
                b.path("privileges").forEach(x -> { for (int q = 0; q < x.path("quantity").asInt(1); q++) got.add(x.path("product_id").asText()); });
                if (!want.equals(got)) fails.add(p + "privileges " + got + " != " + want);
            }
            if (e.has("rules_fired") && !e.get("rules_fired").equals(b.get("rules_fired")))
                fails.add(p + "rules_fired " + b.get("rules_fired") + " != " + e.get("rules_fired"));
            JsonNode mto = b.path("mto_after").path(offerId);
            e.path("mto").fields().forEachRemaining(kv -> {
                if (!kv.getValue().asText().equals(mto.path(kv.getKey()).asText(null)))
                    fails.add(p + "MA_O '" + kv.getKey() + "' = " + mto.path(kv.getKey()).asText(null) + " != " + kv.getValue().asText());
            });
            JsonNode mta = b.path("mta_after_this_bit");
            e.path("mta").fields().forEachRemaining(kv -> {
                if (!kv.getValue().asText().equals(mta.path(kv.getKey()).asText(null)))
                    fails.add(p + "MA_A '" + kv.getKey() + "' = " + mta.path(kv.getKey()).asText(null) + " != " + kv.getValue().asText());
            });
        }
        res.put("passed", fails.size() == 0);
        res.set("failures", fails);
        return res;
    }

    /**
     * Creation-time render, mirroring RulesBuilderImpl.buildDrl / OfferBuilderV2Impl: Freemarker over the
     * blueprint template with offer_input = params + offer_id, re-emitting the runtime placeholders literally.
     */
    static String renderCreationStage(String drl, long offerId, JsonNode params, String mode) throws Exception {
        Map<String, Object> input = new HashMap<>();
        params.fields().forEachRemaining(e -> input.put(e.getKey(), e.getValue().isTextual() ? e.getValue().asText() : e.getValue().toString()));
        if ("replace".equals(mode)) {
            String s = drl;
            for (Map.Entry<String, Object> e : input.entrySet()) s = s.replace("${" + e.getKey() + "}", String.valueOf(e.getValue()));
            return s.replace("${offer_id}", String.valueOf(offerId));
        }
        if ("none".equals(mode)) return drl;
        input.put("offer_id", String.valueOf(offerId));          // OfferBuilderV2Impl.addMandatoryAttributesInMap
        input.put("offer_priority", "${offer_priority}");          // RulesBuilderImpl.buildDrl
        input.put("header_eligibility_check", "${header_eligibility_check}");
        input.put("line_eligibility_check", "${line_eligibility_check}");
        input.putIfAbsent("targeted_segment", "${targeted_segment}");
        return TemplateEngine.getInstance().process(drl, input);
    }

    /** Same build as BaseKieUtilities.generateKie, but returns the KieBuilder messages instead of a generic error. */
    static List<String> kieMessages(String rules, long offerId) {
        KieServices ks = KieServices.Factory.get();
        KieFileSystem kfs = ks.newKieFileSystem();
        kfs.write("src/main/resources/harness-" + offerId + ".drl", rules);
        kfs.generateAndWritePomXML(ks.newReleaseId("com.rule", UUID.randomUUID().toString(), "1.0.0"));
        KieBuilder kb = ks.newKieBuilder(kfs).buildAll();
        List<String> res = new ArrayList<>();
        for (Message m : kb.getResults().getMessages()) {
            res.add(m.getLevel() + " line " + m.getLine() + ":" + m.getColumn() + " " + m.getText().replace('\n', ' '));
        }
        return res;
    }

    static ObjectNode runBit(int idx, JsonNode b, KieBase kieBase, Offer offer, Infra infra, Member member,
                             Map<Long, MemberOffer> memberOffers) throws Exception {
        ObjectNode o = M.createObjectNode();
        o.put("bit_index", idx);

        // flatten {"header":{...},"lines":[...]} into the wire format and use the real extractor
        Map<String, Object> wire = new LinkedHashMap<>();
        if (b.has("header")) {
            wire.putAll(M.convertValue(b.get("header"), Map.class));
            if (b.has("lines")) wire.put("lines", M.convertValue(b.get("lines"), List.class));
            if (b.has("payment_details")) wire.put("payment_details", M.convertValue(b.get("payment_details"), List.class));
        } else {
            wire.putAll(M.convertValue(b, Map.class));
        }
        BitInput bitInput = new BitExtractorImpl().getBitInput(wire);
        // BitController.addLineSequence
        ((com.gravty.bit.input.BitInputImpl) bitInput).setLines(Utilities.addItemSequence(bitInput.getLines()));

        // snapshot for rollback if the BIT fails (production does not persist a failed BIT)
        Member memberSnapshot = deepCopy(member);
        Map<Long, MemberOffer> moSnapshot = deepCopy(new HashMap<>(memberOffers));

        String bitDate = String.valueOf(wire.get("h_bit_date"));
        PointsManagerImpl pm = new PointsManagerImpl(infra.program, infra.dbHelper, false);
        EngineMember engineMember = new EngineMemberImpl(member, bitDate, pm, memberOffers, infra.program.getProgramId());
        BitExecutionContext ctx = new BitExecutionContextImpl(EnvHolder.env(), bitInput, engineMember);

        // Real OfferEligibilityHelperImpl, registered the way RewardKbGenerator/OfferEligibilityHelperImpl(strategy) would
        // (the EligibileOfferStrategy offer-selection filters themselves -- dates, segments, locations, limits -- are NOT run).
        OfferEligibilityHelperImpl elig = new OfferEligibilityHelperImpl();
        String oid = String.valueOf(offer.getOfferID());
        if (!offer.isNonCumulative()) elig.addCumulativeOffer(oid);
        else if (offer.getOfferLevel() == OfferLevelType.HEADER) elig.addHeaderNonCumulativeOffer(oid);
        else if (offer.getOfferLevel() == OfferLevelType.LINE) elig.addLineNonCumulativeOffer(oid);
        ctx.setOfferEligibilityHelper(elig);
        o.put("bit_id", ctx.getBitId());

        List<String> fired = new ArrayList<>();
        KieBase tracked = trackingKieBase(kieBase, fired);
        GravtyRuleBase rb = new GravtyRuleBase() {
            public List<KieBase> getDroolsKb() { return List.of(tracked); }
            public void build(BitExecutionContext c) { }
        };
        PhaseExecutionStatus st = RulesExecutionHelper.Factory.get().execute(ctx, rb); // real StatelessDroolsExecution

        ArrayNode rf = o.putArray("rules_fired");
        fired.forEach(rf::add);

        List<OfferAction> actions = new ArrayList<>(((BitExecutionContextImpl) ctx).getOfferActions());
        if (st.isFailed()) {
            o.put("status", "FAILED");
            ObjectNode err = o.putObject("error");
            err.put("bit_error", String.valueOf(st.getError()));
            Throwable t = st.getException();
            Throwable root = t;
            while (root != null && root.getCause() != null && root.getCause() != root) root = root.getCause();
            err.put("exception", t == null ? null : t.toString());
            err.put("root_cause", root == null ? null : root.toString());
            // roll back: a failed BIT is not persisted
            member.setAttributes(memberSnapshot.getAttributes());
            member.setLoyaltyBalances(memberSnapshot.getLoyaltyBalances());
            memberOffers.clear();
            memberOffers.putAll(moSnapshot);
            o.put("state_rolled_back", true);
        } else {
            o.put("status", "SUCCESS");
            // persistMemberOffers(): offer counts on MemberOffer (real util); MTO values already live in memberOffers
            MemberOfferUtils.updateOfferCount(memberOffers, actions, member.getMemberId(),
                    BitCategory.valueOf(String.valueOf(wire.get("h_bit_category"))), MemberOfferType.MEMBER, false);
        }

        // ---- points grouped by (action, account, tags)
        Map<String, ObjectNode> groups = new LinkedHashMap<>();
        ArrayNode attrUpd = M.createArrayNode();
        ArrayNode privs = M.createArrayNode();
        ArrayNode other = M.createArrayNode();
        for (OfferAction a : actions) {
            String type = a.getType();
            if ("UPDATE_ATTRIBUTE".equals(type)) {
                ObjectNode u = attrUpd.addObject();
                u.put("rule", a.getRuleId());
                u.put("level", a.getAttributeType());
                u.put("name", a.getSubject());
                if (a.getVectorKey() != null) u.put("namespace", a.getVectorKey());
                u.put("operator", a.getOperator());
                if (a.getNewValueS() != null || a.getOldValueS() != null) {
                    u.put("old", a.getOldValueS()); u.put("new", a.getNewValueS());
                } else {
                    u.putPOJO("old", a.getOldValueN()); u.putPOJO("new", a.getNewValueN());
                }
            } else if ("REWARD_PRIVILEGE".equals(type)) {
                ObjectNode p = privs.addObject();
                p.put("rule", a.getRuleId());
                p.put("product_id", a.getSubject());
                p.put("quantity", a.getPrivilegeQuantity());
                p.put("value", a.getValueN());
                ArrayNode codes = p.putArray("codes");
                a.getPrivilegeCodes().forEach(codes::add);
            } else if (type != null && type.endsWith("_POINTS")) {
                String key = type + "|" + a.getSubject() + "|" + String.join(",", a.getRewardTags());
                ObjectNode g = groups.computeIfAbsent(key, k -> {
                    ObjectNode n = M.createObjectNode();
                    n.put("action", type);
                    n.put("account", a.getSubject());
                    n.put("tags", String.join(",", a.getRewardTags()));
                    n.put("total", 0d);
                    n.putArray("rules");
                    return n;
                });
                g.put("total", g.get("total").asDouble() + (a.getValueN() == null ? 0 : a.getValueN()));
                ((ArrayNode) g.get("rules")).add(a.getRuleId());
            } else {
                ObjectNode x = other.addObject();
                x.put("type", type); x.put("rule", a.getRuleId()); x.put("subject", a.getSubject()); x.putPOJO("value", a.getValueN());
            }
        }
        ArrayNode pts = o.putArray("points");
        groups.values().forEach(pts::add);
        o.set("privileges", privs);
        o.set("attribute_updates", attrUpd);
        if (other.size() > 0) o.set("other_actions", other);

        // ---- state after this BIT
        ObjectNode mto = o.putObject("mto_after");
        memberOffers.forEach((k, v) -> {
            ObjectNode n = mto.putObject(String.valueOf(k));
            new TreeMap<>(v.getMtoMap()).forEach(n::put);
        });
        o.set("mta_after_this_bit", M.valueToTree(readTempAttrs(engineMember)));
        ObjectNode balances = o.putObject("loyalty_balances_after");
        new TreeMap<>(member.getLoyaltyBalances()).forEach((k, v) -> balances.put(k, v.toPlainString()));
        ObjectNode mtp = o.putObject("member_attributes_after");
        new TreeMap<>(member.getAttributes()).forEach(mtp::put);
        return o;
    }

    /** Proxy the KieBase so every StatelessKieSession created by StatelessDroolsExecution also records fired rules. */
    static KieBase trackingKieBase(KieBase real, List<String> fired) {
        return (KieBase) Proxy.newProxyInstance(KieBase.class.getClassLoader(), new Class<?>[]{KieBase.class}, (p, m, args) -> {
            Object r = m.invoke(real, args);
            if (m.getName().equals("newStatelessKieSession") && r instanceof StatelessKieSession) {
                ((StatelessKieSession) r).addEventListener(new DefaultAgendaEventListener() {
                    @Override public void afterMatchFired(AfterMatchFiredEvent e) { fired.add(e.getMatch().getRule().getName()); }
                });
            }
            return r;
        });
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> readTempAttrs(EngineMember em) {
        Map<String, Object> res = new TreeMap<>();
        try {
            for (String f : new String[]{"tempAttributesN", "tempAttributesS"}) {
                Field fld = EngineMemberImpl.class.getDeclaredField(f);
                fld.setAccessible(true);
                res.putAll((Map<String, Object>) fld.get(em));
            }
        } catch (Exception ignored) { }
        return res;
    }

    @SuppressWarnings("unchecked")
    static <T> T deepCopy(T o) throws Exception {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        try (ObjectOutputStream oos = new ObjectOutputStream(bos)) { oos.writeObject(o); }
        try (ObjectInputStream ois = new ObjectInputStream(new ByteArrayInputStream(bos.toByteArray()))) { return (T) ois.readObject(); }
    }

    static final class EnvHolder {
        static com.gravty.common.environment.EnvironmentProvider env() {
            return com.gravty.common.environment.EnvironmentProvider.Factory.get();
        }
    }
}
