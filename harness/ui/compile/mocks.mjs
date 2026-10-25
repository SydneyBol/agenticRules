// Local mock backend for the gravty-ui offer rules screen.
// Fixture tenant: program 1, sponsor 10 (matches the engine harness).
// Nothing here is ever sent to a real server; responses are fulfilled in-browser via puppeteer.

export const PROGRAM_ID = 1;
export const SPONSOR_ID = 10;
export const OFFER_ID = 1001;

const attr = (o) => ({
  available_for_rules: true, enabled: true, updatable: false, allow_multiple: false,
  group: 'General', line_level: false, payment_level: false, lookup_type: null,
  counter_type: null, active: true, ...o,
  visible_name: o.visible_name ?? o.name,
});

export const activityAttrs = [
  attr({ id: 1, name: 'h_bit_type', visible_name: 'BIT Type', data_type: 'LOOKUP', lookup_type: 'BIT_TYPE' }),
  attr({ id: 2, name: 'h_location', visible_name: 'Location', data_type: 'LOOKUP', lookup_type: 'LOCATION_CODE' }),
  attr({ id: 3, name: 'h_bit_amount', visible_name: 'BIT Amount', data_type: 'NUMBER' }),
  attr({ id: 4, name: 'h_bit_date', visible_name: 'BIT Date', data_type: 'DATETIME' }),
  attr({ id: 5, name: 'l_quantity', visible_name: 'Line Quantity', data_type: 'NUMBER', line_level: true }),
  attr({ id: 6, name: 'l_amount', visible_name: 'Line Amount', data_type: 'NUMBER', line_level: true }),
  attr({ id: 7, name: 'l_product_external_id', visible_name: 'Line Product External Id', data_type: 'TEXT', line_level: true }),
];

export const memberAttrs = [
  attr({ id: 101, name: 'date_of_birth', visible_name: 'Date of Birth', data_type: 'DATE' }),
  attr({ id: 102, name: 'enrollment_date', visible_name: 'Enrollment Date', data_type: 'DATE' }),
  attr({ id: 103, name: 'tier', visible_name: 'Tier', data_type: 'LOOKUP', lookup_type: 'TIER' }),
];

export const productCustomAttrs = [
  attr({ id: 201, name: 'category', visible_name: 'Category', data_type: 'LOOKUP', lookup_type: 'PRODUCT_CATEGORY', module_type: 'PRODUCT' }),
];

const lk = (type, code, name) => ({ id: `${type}_${code}`, type, code, name, active: true, description: name });
export const lookups = [
  lk('BIT_TYPE', 'SPEND', 'Spend'), lk('BIT_TYPE', 'STAY', 'Stay'), lk('BIT_TYPE', 'BOOKING', 'Booking'),
  ...['L1', 'L2', 'L3', 'AP1', 'AP2'].map((c) => lk('LOCATION_CODE', c, c)),
  lk('TIER', 'GOLD', 'Gold'), lk('TIER', 'SILVER', 'Silver'), lk('TIER', 'BLUE', 'Blue'),
  lk('PRODUCT_CATEGORY', 'SNACKS', 'Snacks'), lk('PRODUCT_CATEGORY', 'DRINKS', 'Drinks'), lk('PRODUCT_CATEGORY', 'BAKERY', 'Bakery'),
];

export const bitTypes = [
  { id: 1, name: 'Spend', code: 'SPEND', active: true },
  { id: 2, name: 'Stay', code: 'STAY', active: true },
  { id: 3, name: 'Booking', code: 'BOOKING', active: true },
];

export const loyaltyAccount = {
  id: 1, loyalty_currency: 'Points', status: 'Active', currency_type: 'Points', name: 'Points',
  sponsor: SPONSOR_ID, is_base_account: true, enable_pending_points: false, expiration_type: 'NONE', active: true,
};
export const tags = [
  { id: 1, loyalty_account: 1, name: 'BASE', code: 'BASE', active: true },
  { id: 2, loyalty_account: 1, name: 'BONUS', code: 'BONUS', active: true },
];

export const products = [{
  id: 501, name: 'Free Coffee', dynamic: false, privilege_type: 'Fixed-Product', bundle_type: 'static',
  product_choices: [], active: true, sponsor: SPONSOR_ID, sponsors: [SPONSOR_ID],
  usage_policy: [{ id: 9, policy_name: 'Default policy', default: true, active: true }],
}];

export const lovs = [
  { id: 1, name: 'SPONSOR_LIST', api_url: '/api/v1/trim/sponsors/', display: 'name', code: 'id' },
];

export const sponsors = [{ id: SPONSOR_ID, name: 'Fixture Sponsor', sponsor_name: 'Fixture Sponsor', is_host: true, status: 'A', code: 'SP10' }];

export const program = [{
  id: PROGRAM_ID, name: 'Fixture Program', timezone: 'UTC', date_format: 'DD/MM/YYYY',
  currency: 'USD', program_currency: 'USD', time_format: 'HH:mm',
}];

export function offer(status = 'planning') {
  return {
    id: OFFER_ID, name: 'UI harness offer', offer_type: 'reward', status,
    sponsors: [SPONSOR_ID], primary_sponsor: SPONSOR_ID, program: PROGRAM_ID,
    is_individualized: false, is_template_based: false, is_blueprint_based: false, template: null,
    is_base_offer: false, start_date: '2026-01-01T00:00:00Z', end_date: '2027-01-01T00:00:00Z',
    budget_info: null, offer_category: null,
  };
}

export function mtos(counters) {
  return counters.map((c, i) => ({
    id: 900 + i, offer: OFFER_ID, name: c.name, visible_name: c.name, data_type: c.data_type,
    level: 'OFFER', counter_type: c.counter_type, target_type: c.target_type ?? 'NO TARGET',
    activity_id: 1, active: true, available_for_rules: true, enabled: true,
    target_value: {},
    // bolapi offers/serializers.py validate_entity_attribute_map: {"Activity": ["attr"], "Member": [...], "Product": [...]}
    entity_attribute_map: c.counter_type === 'VECTOR' && c.vector_namespace
      ? { [c.vector_level || 'Activity']: [c.vector_namespace] } : null,
  }));
}

export const globals = {
  currentUser: {
    id: 1, userval: 'harness', user: 'harness', token_key: 'mock-local-token-not-real',
    role: {
      code: 'PROGRAM_MANAGER', name: 'Program Manager', access_level: 'Program',
      can_view_offer: true, can_edit_offer: true, can_create_offer: true, can_view_dashboard: false,
    },
    is_sponsoruser: false, is_superuser: false, sponsor_id: SPONSOR_ID, sponosors_list: sponsors,
    featureSwitch: {}, role_name: 'Program Manager', programId: PROGRAM_ID, compass_access: false,
  },
  aiFeaturesToggle: [],
};

// Router: (method, url, body, ctx) -> {status, body} | null (null = unknown)
export function route(method, u, body, ctx) {
  const p = u.pathname;
  const m = (re) => p.match(re);
  if (method === 'GET') {
    if (m(/\/features\/\d+\/?$/) || m(/\/features\/?$/)) return [];
    if (m(/\/attributelibrary\/activity\/$/)) return activityAttrs;
    if (m(/\/attributelibrary\/member\/$/)) return memberAttrs;
    if (m(/\/attributelibrary\/custom\/$/)) {
      const mt = u.searchParams.get('module_type');
      if (mt === 'PRODUCT') return productCustomAttrs;
      return [];
    }
    if (m(/\/lookups\/$/)) {
      const t = u.searchParams.get('type');
      return t ? lookups.filter((l) => l.type === t) : lookups;
    }
    if (m(/\/bit-types\/$/) || m(/\/bittypes\/$/)) return bitTypes;
    if (m(/\/loyaltyaccounts\/1\/tags\/$/)) return tags;
    if (m(/\/loyaltyaccounts\/\d+\/tags\/$/)) return [];
    if (m(/\/sponsors\/loyaltyaccounts\/$/)) return { loyalty_accounts: [loyaltyAccount] };
    if (m(/\/loyaltyaccounts\/$/)) return [loyaltyAccount];
    if (m(/\/product\/$/)) return products;
    if (m(/\/trim\/sponsors\/$/)) return sponsors;
    if (m(/\/programs\/$/)) return program;
    if (m(new RegExp(`/offers/${OFFER_ID}/member-tracked-attributes/$`))) return mtos(ctx.counters);
    if (m(new RegExp(`/offers/${OFFER_ID}/drl/$`))) return ctx.drlGet ?? [];
    if (m(new RegExp(`/offers/${OFFER_ID}/segment-attributes/$`))) return [];
    if (m(new RegExp(`/offers/${OFFER_ID}/$`))) return offer();
    if (m(/\/lovs\/$/)) return lovs; // NB: [] here makes forkJoin({}) complete without emitting -> screen never finishes init
    if (m(/\/tenants\/basic-info\/$/)) return { name: 'harness' };
    return null;
  }
  if (method === 'POST') {
    if (m(new RegExp(`/offers/${OFFER_ID}/member-tracked-attributes/$`))) {
      const b = safeJson(body);
      return { id: 990 + Math.floor(Math.random() * 1000), offer: OFFER_ID, ...b };
    }
    if (m(/\/ai\//) || m(/ai-prompt|prompt/)) return { prompt: '' };
    return null;
  }
  return null;
}

function safeJson(s) { try { return JSON.parse(s); } catch { return {}; } }
