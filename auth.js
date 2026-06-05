/**
 * SmartBalju Auth + Data Layer v2.0
 * Firebase Realtime Database 연동
 * 
 * 사용법: 모든 HTML 파일에서 <script src="auth.js"></script> 로 로드
 * 전역 객체: window.SmartBaljuAuth
 */

// ── Firebase 설정 ──────────────────────────────────────────
const FIREBASE_CONFIG = {
  apiKey:            "AIzaSyBNSCDGJthpy6rwtFZ1HwpW30Q4_j1b9KU",
  authDomain:        "smart-balju.firebaseapp.com",
  databaseURL:       "https://smart-balju-default-rtdb.asia-southeast1.firebasedatabase.app",
  projectId:         "smart-balju",
  storageBucket:     "smart-balju.firebasestorage.app",
  messagingSenderId: "139221840931",
  appId:             "1:139221840931:web:9949b064807526e3f92d96",
};

// ── Firebase SDK (CDN compat) ───────────────────────────────
// Firebase 앱 초기화 (중복 방지)
let _db = null;
let _firebaseReady = false;
let _firebaseError = null;

(function initFirebase() {
  // 이미 로드된 경우
  if (typeof firebase !== 'undefined') {
    try {
      if (!firebase.apps.length) firebase.initializeApp(FIREBASE_CONFIG);
      _db = firebase.database();
      _firebaseReady = true;
    } catch (e) {
      _firebaseError = e;
      console.warn('[auth.js] Firebase init error, localStorage fallback 사용:', e);
    }
    return;
  }

  // Firebase SDK 동적 로드
  const scripts = [
    'https://www.gstatic.com/firebasejs/9.23.0/firebase-app-compat.js',
    'https://www.gstatic.com/firebasejs/9.23.0/firebase-database-compat.js',
  ];

  let loaded = 0;
  scripts.forEach(src => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = () => {
      loaded++;
      if (loaded === scripts.length) {
        try {
          if (!firebase.apps.length) firebase.initializeApp(FIREBASE_CONFIG);
          _db = firebase.database();
          _firebaseReady = true;
          // 로드 완료 이벤트
          document.dispatchEvent(new CustomEvent('firebaseReady'));
        } catch (e) {
          _firebaseError = e;
          console.warn('[auth.js] Firebase 초기화 실패, localStorage 사용:', e);
          document.dispatchEvent(new CustomEvent('firebaseReady'));
        }
      }
    };
    s.onerror = () => {
      loaded++;
      _firebaseError = new Error('Firebase SDK 로드 실패');
      if (loaded === scripts.length) document.dispatchEvent(new CustomEvent('firebaseReady'));
    };
    document.head.appendChild(s);
  });
})();

// ── 헬퍼 ────────────────────────────────────────────────────
function genId() {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
}
function now() {
  return new Date().toISOString();
}
function hashPw(pw) {
  // 간단 해시 (실서비스는 서버사이드 처리 필요)
  let h = 0;
  for (let i = 0; i < pw.length; i++) h = (Math.imul(31, h) + pw.charCodeAt(i)) | 0;
  return 'h_' + Math.abs(h).toString(16);
}

// ── Firebase vs localStorage 추상화 ────────────────────────
const DB = {
  // 읽기
  async get(path) {
    if (_db) {
      const snap = await _db.ref(path).get();
      return snap.exists() ? snap.val() : null;
    }
    const key = 'sbalju_' + path.replace(/\//g, '_');
    const v = localStorage.getItem(key);
    return v ? JSON.parse(v) : null;
  },
  // 쓰기 (덮어쓰기)
  async set(path, val) {
    if (_db) {
      await _db.ref(path).set(val);
      return;
    }
    const key = 'sbalju_' + path.replace(/\//g, '_');
    localStorage.setItem(key, JSON.stringify(val));
  },
  // 부분 업데이트
  async update(path, val) {
    if (_db) {
      await _db.ref(path).update(val);
      return;
    }
    const existing = await DB.get(path) || {};
    await DB.set(path, { ...existing, ...val });
  },
  // 삭제
  async remove(path) {
    if (_db) {
      await _db.ref(path).remove();
      return;
    }
    const key = 'sbalju_' + path.replace(/\//g, '_');
    localStorage.removeItem(key);
  },
  // 실시간 구독
  on(path, cb) {
    if (_db) {
      _db.ref(path).on('value', snap => cb(snap.exists() ? snap.val() : null));
      return () => _db.ref(path).off('value');
    }
    // localStorage fallback: 폴링
    const iv = setInterval(async () => cb(await DB.get(path)), 3000);
    return () => clearInterval(iv);
  },
};

// ── 상수 ────────────────────────────────────────────────────
const ROLE_LABELS = {
  master:    'Master',
  hq:        '프랜차이즈 본사',
  franchise: '가맹점',
  supplier:  '납품업체',
  driver:    '배송기사',
};

const STATUS_LABELS = {
  active:                   '정상',
  inactive:                 '비활성',
  suspended:                '사용중지',
  expired:                  '계약만료',
  password_reset_required:  '비밀번호 재설정 필요',
};

const BLOCKED_LOGIN_STATUSES = ['inactive', 'suspended', 'expired'];

// ── 세션 (sessionStorage) ──────────────────────────────────
const SESSION_KEY = 'sbalju_session';

function getSession() {
  try { return JSON.parse(sessionStorage.getItem(SESSION_KEY)); } catch { return null; }
}
function setSession(user) {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(user));
}
function clearSession() {
  sessionStorage.removeItem(SESSION_KEY);
}

// ── 사용자 CRUD ────────────────────────────────────────────
async function loadUsers() {
  const data = await DB.get('users') || {};
  const users = Object.values(data);
  // 마스터 계정 없으면 자동 생성
  if (!users.find(u => u.role === 'master')) {
    await _ensureMaster();
    return Object.values(await DB.get('users') || {});
  }
  return users;
}

async function _ensureMaster() {
  const id = 'master_001';
  const existing = await DB.get('users/' + id);
  if (existing) return;
  await DB.set('users/' + id, {
    uid: id,
    role: 'master',
    status: 'active',
    loginId: 'n41u0912',
    passwordHash: hashPw('1234'),
    name: '관리자',
    businessName: '대상정보통신',
    businessNumber: '',
    phone: '',
    brandId: '',
    storeId: '',
    supplierId: '',
    driverId: '',
    permissions: {},
    mustChangePassword: false,
    createdAt: now(),
    lastLoginAt: null,
  });
}

async function upsertUser(fields) {
  const uid = fields.uid || genId();
  if (!fields.uid && fields.loginId) {
    const allUsers = await loadUsers();
    if (allUsers.some(u => u.loginId === fields.loginId)) {
      throw new Error('이미 사용 중인 아이디입니다: ' + fields.loginId);
    }
  }
  const existing = await DB.get('users/' + uid) || {};
  const pw = fields.password ? hashPw(fields.password) : existing.passwordHash;
  const user = {
    ...existing,
    uid,
    role:           fields.role           ?? existing.role ?? 'franchise',
    status:         fields.status         ?? existing.status ?? 'active',
    loginId:        fields.loginId        ?? existing.loginId ?? '',
    passwordHash:   pw,
    name:           fields.name           ?? existing.name ?? '',
    businessName:   fields.businessName   ?? existing.businessName ?? '',
    businessNumber: fields.businessNumber ?? existing.businessNumber ?? '',
    phone:          fields.phone          ?? existing.phone ?? '',
    brandId:        fields.brandId        ?? existing.brandId ?? '',
    storeId:        fields.storeId        ?? existing.storeId ?? '',
    supplierId:     fields.supplierId     ?? existing.supplierId ?? '',
    driverId:       fields.driverId       ?? existing.driverId ?? '',
    permissions:    existing.permissions  ?? {},
    okposId:        fields.okposId        ?? existing.okposId        ?? '',
    okposPw:        fields.okposPw        ? hashPw(fields.okposPw) : (existing.okposPw ?? ''),
    mustChangePassword: fields.password ? false : (existing.mustChangePassword ?? false),
    createdAt:      existing.createdAt    ?? now(),
    updatedAt:      now(),
    lastLoginAt:    existing.lastLoginAt  ?? null,
  };
  await DB.set('users/' + uid, user);
  return user;
}

async function deleteUser(uid) {
  await DB.remove('users/' + uid);
}

async function setUserStatus(uid, status) {
  await DB.update('users/' + uid, { status, updatedAt: now() });
}

async function resetPasswordByMaster(uid) {
  const tempPw = Math.random().toString(36).slice(2, 8).toUpperCase();
  await DB.update('users/' + uid, {
    passwordHash: hashPw(tempPw),
    mustChangePassword: true,
    updatedAt: now(),
  });
  const user = await DB.get('users/' + uid);
  return { user, tempPassword: tempPw };
}

// ── 권한 관리 ───────────────────────────────────────────────
async function setPermissions(uid, perms) {
  await DB.update('users/' + uid, { permissions: perms, updatedAt: now() });
}
async function getPermissions(uid) {
  const user = await DB.get('users/' + uid);
  return user?.permissions || {};
}

// ── 로그인 ──────────────────────────────────────────────────
async function login(loginId, password) {
  const users = await loadUsers();
  const matches = users.filter(u => u.loginId === loginId);
  if (!matches.length) return { ok: false, error: '존재하지 않는 아이디입니다.' };
  if (matches.length > 1) console.warn('[auth] loginId 중복 감지:', loginId, matches.length + '개');
  const user = matches.find(u => u.passwordHash === hashPw(password)) || matches[0];
  if (user.passwordHash !== hashPw(password)) return { ok: false, error: '비밀번호가 틀렸습니다.' };
  if (BLOCKED_LOGIN_STATUSES.includes(user.status)) {
    return { ok: false, error: STATUS_LABELS[user.status] + ' 상태입니다. 관리자에게 문의하세요.' };
  }
  await DB.update('users/' + user.uid, { lastLoginAt: now() });
  user.lastLoginAt = now();
  setSession(user);
  return { ok: true, user };
}

function logout() {
  clearSession();
  location.href = '/';
}

// ── 역할별 리다이렉트 ────────────────────────────────────────
function redirectByRole(user) {
  const map = {
    master:    '/master.html',
    hq:        '/site_hq.html',
    franchise: '/site_franchise.html',
    supplier:  '/site_supplier.html',
    driver:    '/site_driver.html',
  };
  location.href = map[user.role] || '/';
}

// ── 페이지 보호 (각 페이지 상단에서 호출) ──────────────────
function requireLogin(allowedRoles) {
  const session = getSession();
  if (!session) { location.href = '/'; return null; }
  if (allowedRoles && !allowedRoles.includes(session.role)) {
    alert('접근 권한이 없습니다.');
    location.href = '/';
    return null;
  }
  return session;
}

// ── 거래처 (납품업체 계정 첫 로그인 시 본사에 표시) ────────
async function registerPartner(fields) {
  const id = genId();
  const partner = {
    id, ...fields,
    status: fields.status || 'pending_approval',
    createdAt: now(),
  };
  await DB.set('partners/' + id, partner);
  return partner;
}
async function loadPartners() {
  const data = await DB.get('partners') || {};
  return Object.values(data);
}
async function updatePartner(id, fields) {
  await DB.update('partners/' + id, { ...fields, updatedAt: now() });
}
async function deletePartner(id) {
  await DB.remove('partners/' + id);
}

// ── 브랜드 ──────────────────────────────────────────────────
async function registerBrand(fields) {
  const id = genId();
  const brand = { id, ...fields, createdAt: now() };
  await DB.set('brands/' + id, brand);
  return brand;
}
async function loadBrands() {
  const data = await DB.get('brands') || {};
  return Object.values(data);
}

// ── 메뉴 ────────────────────────────────────────────────────
async function registerMenu(fields) {
  const id = genId();
  const menu = { id, ...fields, createdAt: now() };
  await DB.set('menus/' + id, menu);
  return menu;
}
async function loadMenus() {
  const data = await DB.get('menus') || {};
  return Object.values(data);
}
async function updateMenu(id, fields) {
  await DB.update('menus/' + id, { ...fields, updatedAt: now() });
}
async function deleteMenu(id) {
  await DB.remove('menus/' + id);
}

// ── 판매가격 ─────────────────────────────────────────────────
async function registerPrice(fields) {
  const id = genId();
  const price = { id, ...fields, createdAt: now() };
  await DB.set('prices/' + id, price);
  return price;
}
async function loadPrices() {
  const data = await DB.get('prices') || {};
  return Object.values(data);
}
async function updatePrice(id, fields) {
  await DB.update('prices/' + id, { ...fields, updatedAt: now() });
}
async function deletePrice(id) {
  await DB.remove('prices/' + id);
}

// ── 납품업체: 배송기사 등록 ─────────────────────────────────
async function registerSupplierDriver(fields) {
  const id = genId();
  const driver = { id, ...fields, createdAt: now() };
  await DB.set('supplier_drivers/' + id, driver);
  return driver;
}
async function loadSupplierDrivers() {
  const data = await DB.get('supplier_drivers') || {};
  return Object.values(data);
}
async function deleteSupplierDriver(id) {
  await DB.remove('supplier_drivers/' + id);
}

// ── 납품업체: 가맹점(배송지) 등록 ──────────────────────────
async function registerSupplierFranchise(fields) {
  const id = genId();
  const store = { id, ...fields, createdAt: now() };
  await DB.set('supplier_franchises/' + id, store);
  return store;
}
async function loadSupplierFranchises() {
  const data = await DB.get('supplier_franchises') || {};
  return Object.values(data);
}
async function deleteSupplierFranchise(id) {
  await DB.remove('supplier_franchises/' + id);
}

// ── 배송 ────────────────────────────────────────────────────
async function createDelivery(fields) {
  const id = genId();
  const delivery = {
    id,
    storeId:             fields.storeId             || '',
    storeName:           fields.storeName           || '',
    storeBusinessNumber: fields.storeBusinessNumber || '',
    storeAddress:        fields.storeAddress        || fields.address || '',
    storePhone:          fields.storePhone          || '',
    storeOwner:          fields.storeOwner          || '',
    address:             fields.storeAddress        || fields.address || '',
    brandId:             fields.brandId             || '',
    brandName:           fields.brandName           || '',
    supplierName:        fields.supplierName        || '',
    driverId:            fields.driverId            || '',
    driverName:          fields.driverName          || '',
    driverPhone:         fields.driverPhone         || '',
    items:               fields.items               || '',
    itemDetails:         fields.itemDetails         || [],
    amount:              fields.amount              || 0,
    fromOrderId:         fields.fromOrderId         || '',
    status:              fields.status              || 'assigned',
    note:                fields.note               || '',
    createdAt:           now(),
    startedAt:           null,
    doneAt:              null,
    issueNote:           null,
  };
  await DB.set('deliveries/' + id, delivery);
  return delivery;
}

async function loadDeliveries(filters = {}) {
  const data = await DB.get('deliveries') || {};
  let list = Object.values(data);
  if (filters.driverId) list = list.filter(d => d.driverId === filters.driverId);
  if (filters.storeId)  list = list.filter(d => d.storeId  === filters.storeId);
  if (filters.status)   list = list.filter(d => d.status   === filters.status);
  return list.sort((a, b) => a.createdAt > b.createdAt ? 1 : -1);
}

async function assignDelivery(deliveryId, driverId) {
  const drivers = await loadSupplierDrivers();
  const driver = drivers.find(d => d.id === driverId);
  await DB.update('deliveries/' + deliveryId, {
    driverId,
    driverName:  driver?.name  || '',
    driverPhone: driver?.phone || '',
    status: 'assigned',
    updatedAt: now(),
  });
}

async function startDelivery(deliveryId) {
  await DB.update('deliveries/' + deliveryId, {
    status: 'shipping',
    startedAt: now(),
    updatedAt: now(),
  });
}

async function markDeliveryDone(deliveryId, note = '') {
  await DB.update('deliveries/' + deliveryId, {
    status: 'done',
    doneAt: now(),
    issueNote: note || null,
    updatedAt: now(),
  });
}

async function reportDeliveryIssue(deliveryId, note) {
  await DB.update('deliveries/' + deliveryId, {
    status: 'issue',
    issueNote: note,
    updatedAt: now(),
  });
}

// 배송 링크 생성 (배송기사용)
function getDeliveryLink(deliveryId) {
  const base = location.origin;
  return `${base}/site_driver.html?delivery=${deliveryId}`;
}

// 배송전표 HTML 생성
async function getDeliverySlipHtml(deliveryId) {
  const d = await DB.get('deliveries/' + deliveryId);
  if (!d) return '';
  return `
    <!DOCTYPE html><html><head><meta charset="UTF-8">
    <title>배송전표</title>
    <style>body{font-family:'Noto Sans KR',sans-serif;padding:24px;max-width:400px;margin:0 auto}
    h2{font-size:18px;margin-bottom:16px}
    .row{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid #eee;font-size:14px}
    .label{color:#888}.val{font-weight:700}
    .items{margin-top:12px;padding:12px;background:#f5f5f5;border-radius:8px;font-size:13px}
    .footer{margin-top:20px;text-align:center;color:#aaa;font-size:11px}
    @media print{button{display:none}}</style></head>
    <body>
    <h2>📦 배송전표</h2>
    <div class="row"><span class="label">배송ID</span><span class="val">${d.id}</span></div>
    <div class="row"><span class="label">배송처</span><span class="val">${d.storeName}</span></div>
    <div class="row"><span class="label">주소</span><span class="val">${d.address}</span></div>
    <div class="row"><span class="label">기사</span><span class="val">${d.driverName || '미배정'}</span></div>
    <div class="row"><span class="label">연락처</span><span class="val">${d.driverPhone || '-'}</span></div>
    <div class="row"><span class="label">금액</span><span class="val">${(d.amount||0).toLocaleString()}원</span></div>
    <div class="row"><span class="label">생성일시</span><span class="val">${d.createdAt?.slice(0,16).replace('T',' ')}</span></div>
    <div class="items"><b>품목:</b><br>${d.items || '-'}</div>
    <div class="footer">대상정보통신 스마트발주</div>
    <br><button onclick="window.print()">🖨️ 인쇄</button>
    </body></html>`;
}

// ── 실시간 구독 헬퍼 ────────────────────────────────────────
function onDeliveriesChange(cb, filters = {}) {
  return DB.on('deliveries', data => {
    let list = Object.values(data || {});
    if (filters.driverId) list = list.filter(d => d.driverId === filters.driverId);
    if (filters.status)   list = list.filter(d => d.status   === filters.status);
    cb(list);
  });
}

function onUsersChange(cb) {
  return DB.on('users', data => cb(Object.values(data || {})));
}

// ── localStorage 동기 호환 (기존 코드 지원) ────────────────
// 기존 master.html, site_hq.html 등에서 동기 방식으로 쓰던 loadData() 지원
function loadData() {
  // 동기 fallback (localStorage)
  const keys = ['partners','brands','menus','prices','supplier_drivers','supplier_franchises','deliveries'];
  const out = {};
  keys.forEach(k => {
    try {
      const v = localStorage.getItem('sbalju_' + k);
      out[k] = v ? Object.values(JSON.parse(v)) : [];
    } catch { out[k] = []; }
  });
  return out;
}

// ── 전역 노출 ────────────────────────────────────────────────
window.SmartBaljuAuth = {
  // 상수
  ROLE_LABELS,
  STATUS_LABELS,
  BLOCKED_LOGIN_STATUSES,

  // DB 직접 접근
  DB,

  // 세션
  getSession,
  setSession,
  clearSession,
  requireLogin,

  // 인증
  login,
  logout,
  redirectByRole,
  hashPw,

  // 사용자
  loadUsers,
  upsertUser,
  deleteUser,
  setUserStatus,
  resetPasswordByMaster,
  activateUser:   uid => setUserStatus(uid, 'active'),
  deactivateUser: uid => setUserStatus(uid, 'inactive'),
  suspendUser:    uid => setUserStatus(uid, 'suspended'),
  expireUser:     uid => setUserStatus(uid, 'expired'),

  // 권한
  setPermissions,
  getPermissions,

  // 거래처 (납품업체)
  registerPartner,
  loadPartners,
  updatePartner,
  deletePartner,

  // 브랜드
  registerBrand,
  loadBrands,

  // 메뉴
  registerMenu,
  loadMenus,
  updateMenu,
  deleteMenu,

  // 판매가격
  registerPrice,
  loadPrices,
  updatePrice,
  deletePrice,

  // 납품업체: 배송기사
  registerSupplierDriver,
  loadSupplierDrivers,
  deleteSupplierDriver,

  // 납품업체: 가맹점
  registerSupplierFranchise,
  loadSupplierFranchises,
  deleteSupplierFranchise,

  // 배송
  createDelivery,
  loadDeliveries,
  assignDelivery,
  startDelivery,
  markDeliveryDone,
  reportDeliveryIssue,
  getDeliveryLink,
  getDeliverySlipHtml,
  onDeliveriesChange,
  onUsersChange,

  // 기존 호환
  loadData,

  // Firebase 상태
  get isFirebaseReady() { return _firebaseReady; },
  get db() { return _db; },
};

// ── 초기화 로그 ─────────────────────────────────────────────
document.addEventListener('firebaseReady', () => {
  if (_firebaseReady) {
    console.log('[SmartBalju] ✅ Firebase Realtime Database 연결됨');
    _ensureMaster();
  } else {
    console.warn('[SmartBalju] ⚠️ Firebase 미연결 — localStorage 모드로 동작');
  }
});
