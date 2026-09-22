import { useState, useEffect } from 'react';
import './AutoLogin.css'; // reuse same styles

const API = 'http://127.0.0.1:8080';

export default function GoogleFlowLogin() {
  const [account, setAccount] = useState({
    email: '',
    password: '',
    totp_secret: '',
    password_configured: false,
    totp_configured: false,
    session_configured: false,
  });
  const [browserStatus, setBrowserStatus] = useState(null);
  const [loadingMsg, setLoadingMsg] = useState('');
  const [resultMsg, setResultMsg] = useState('');
  const [resultType, setResultType] = useState('success');

  const readApiResponse = async (response) => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || `HTTP ${response.status}`);
    }
    return data;
  };

  const fetchStatus = async (hydrateCredentials = false) => {
    try {
      const [accRes, brRes] = await Promise.all([
        fetch(`${API}/api/flow/account`),
        fetch(`${API}/api/flow/browser`),
      ]);
      const [accData, brData] = await Promise.all([
        readApiResponse(accRes),
        readApiResponse(brRes),
      ]);
      setAccount(prev => ({
        ...prev,
        email: hydrateCredentials ? (accData.email || '') : prev.email,
        password: hydrateCredentials ? '' : prev.password,
        totp_secret: hydrateCredentials ? '' : prev.totp_secret,
        password_configured: Boolean(accData.password_configured),
        totp_configured: Boolean(accData.totp_configured),
        session_configured: Boolean(accData.session_configured),
      }));
      setBrowserStatus(brData);
    } catch (err) {
      console.error('Lỗi lấy thông tin Flow:', err);
    }
  };

  useEffect(() => {
    fetchStatus(true);
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setAccount(prev => ({ ...prev, [name]: value }));
  };

  const handleSave = async () => {
    const email = account.email.trim();
    if (!email) {
      setResultType('error');
      setResultMsg('❌ Email Google là bắt buộc.');
      return;
    }
    try {
      setLoadingMsg('Đang lưu thông tin tài khoản...');
      setResultMsg('');
      const response = await fetch(`${API}/api/flow/account`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email,
          password: account.password,
          totp_secret: account.totp_secret,
        }),
      });
      const data = await readApiResponse(response);
      setAccount(prev => ({
        ...prev,
        email: data.email || email,
        password: '',
        totp_secret: '',
        password_configured: Boolean(data.password_configured),
        totp_configured: Boolean(data.totp_configured),
        session_configured: Boolean(data.session_configured),
      }));
      setResultType('success');
      setResultMsg('✅ Đã lưu thông tin tài khoản Google Flow!');
    } catch (error) {
      setResultType('error');
      setResultMsg(`❌ Lỗi khi lưu thông tin: ${error.message}`);
    } finally {
      setLoadingMsg('');
    }
  };

  const handleClear = async () => {
    if (!confirm('Xác nhận xóa toàn bộ thông tin đăng nhập Google Flow?')) return;
    try {
      setLoadingMsg('Đang xóa...');
      const response = await fetch(`${API}/api/flow/account`, { method: 'DELETE' });
      await readApiResponse(response);
      setAccount({ email: '', password: '', totp_secret: '', password_configured: false, totp_configured: false, session_configured: false });
      setResultType('success');
      setResultMsg('✅ Đã xóa thông tin Google Flow.');
    } catch (error) {
      setResultType('error');
      setResultMsg(`❌ Lỗi khi xóa: ${error.message}`);
    } finally {
      setLoadingMsg('');
    }
  };

  const handleBrowser = async (action) => {
    const labels = {
      start: 'Đang khởi động trình duyệt Flow...',
      stop: 'Đang dừng trình duyệt Flow...',
      show: 'Đang hiện trình duyệt Flow...',
      hide: 'Đang ẩn trình duyệt Flow...'
    };
    try {
      setLoadingMsg(labels[action] || '...');
      setResultMsg('');
      const response = await fetch(`${API}/api/flow/browser/${action}`, { method: 'POST' });
      const data = await readApiResponse(response);
      setBrowserStatus(data);
      setResultType('success');
      const messages = {
        start: '🌐 Trình duyệt Flow đã khởi động.',
        stop: '⏹️ Trình duyệt Flow đã dừng.',
        show: '👁 Trình duyệt Flow đang hiển thị trên màn hình.',
        hide: '🙈 Trình duyệt Flow đã được ẩn vào nền.'
      };
      setResultMsg(messages[action] || 'Thao tác thành công.');
    } catch (error) {
      setResultType('error');
      setResultMsg(`❌ Lỗi thao tác trình duyệt: ${error.message}`);
    } finally {
      setLoadingMsg('');
    }
  };

  const handleCheckLogin = async () => {
    try {
      setLoadingMsg('⏳ Đang kiểm tra phiên đăng nhập Google Flow...');
      setResultMsg('');
      const res = await fetch(`${API}/api/flow/login-check`, { method: 'POST' });
      const data = await readApiResponse(res);
      if (data.success) {
        setResultType('success');
        setResultMsg(`✅ Đã đăng nhập Google Flow thành công! ${data.message || ''}`);
        await fetchStatus();
      } else {
        setResultType('error');
        setResultMsg(`❌ Chưa đăng nhập: ${data.error || 'Phiên hết hạn'}`);
      }
    } catch (error) {
      setResultType('error');
      setResultMsg(`❌ Lỗi kết nối backend: ${error.message}`);
    } finally {
      setLoadingMsg('');
    }
  };

  const isConnected = browserStatus?.connected;
  const isAlive = browserStatus?.process_alive;

  return (
    <div className="auto-login-container">
      <h1 className="hero-title">Google Flow (Veo3) Login</h1>
      <p className="hero-subtitle">
        Quản lý tài khoản Google để tạo ảnh/video bằng Google Flow thay thế ComfyUI.
      </p>

      {/* Browser Status */}
      <div className="login-panel" style={{ marginBottom: '16px' }}>
        <h3 style={{ margin: '0 0 12px', color: '#ccc', fontSize: '1em' }}>Trạng thái Trình duyệt Flow</h3>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{
            padding: '4px 12px', borderRadius: '20px', fontSize: '0.85em', fontWeight: 600,
            background: isConnected ? '#1a472a' : isAlive ? '#4a3800' : '#3a1a1a',
            color: isConnected ? '#4ade80' : isAlive ? '#facc15' : '#f87171',
          }}>
            {isConnected ? '🟢 Đang kết nối' : isAlive ? '🟡 Đang khởi động' : '🔴 Chưa chạy'}
          </span>
          {browserStatus?.cdp_url && (
            <span style={{ fontSize: '0.8em', color: '#888' }}>CDP: {browserStatus.cdp_url}</span>
          )}
          <div style={{ marginLeft: 'auto', display: 'flex', gap: '8px' }}>
            <button
              className="btn-secondary"
              style={{ width: 'auto', padding: '8px 16px' }}
              onClick={() => handleBrowser(browserStatus?.window_visible ? 'hide' : 'show')}
            >
              {browserStatus?.window_visible ? '🙈 Ẩn trình duyệt' : '👁 Hiện trình duyệt'}
            </button>
            {!isAlive && (
              <button className="btn-primary" style={{ width: 'auto', padding: '8px 16px' }} onClick={() => handleBrowser('show')}>
                ▶ Khởi động & Mở
              </button>
            )}
            {isAlive && (
              <button className="btn-secondary" style={{ width: 'auto', padding: '8px 16px' }} onClick={() => handleBrowser('stop')}>
                ⏹ Dừng
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Account Credentials */}
      <div className="login-panel">
        <h3 style={{ margin: '0 0 16px', color: '#ccc', fontSize: '1em' }}>Thông tin tài khoản Google</h3>

        <div className="form-group">
          <label>Email Google:</label>
          <input
            type="email"
            name="email"
            value={account.email}
            onChange={handleChange}
            placeholder="Nhập email Google"
          />
        </div>

        <div className="form-group">
          <label>Mật khẩu Google:</label>
          <input
            type="password"
            name="password"
            value={account.password}
            onChange={handleChange}
            placeholder={account.password_configured ? 'Đã lưu — để trống để giữ nguyên' : 'Nhập mật khẩu...'}
          />
        </div>

        <div className="form-group">
          <label>Mã bảo mật 2FA (TOTP Secret) — Tùy chọn:</label>
          <input
            type="password"
            name="totp_secret"
            value={account.totp_secret}
            onChange={handleChange}
            placeholder={account.totp_configured ? 'Đã lưu — để trống để giữ nguyên' : 'ABC123XYZ...'}
          />
        </div>

        {/* Status badges */}
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '16px' }}>
          {[
            { label: 'Email', ok: Boolean(account.email.trim()) },
            { label: 'Mật khẩu', ok: account.password_configured },
            { label: 'TOTP 2FA', ok: account.totp_configured },
            { label: 'Cookie phiên', ok: account.session_configured },
          ].map(({ label, ok }) => (
            <span key={label} style={{
              padding: '3px 10px', borderRadius: '12px', fontSize: '0.8em',
              background: ok ? '#1a3a2a' : '#2a1a1a',
              color: ok ? '#4ade80' : '#888',
            }}>
              {ok ? '✓' : '✗'} {label}
            </span>
          ))}
        </div>

        <p className="help-text" style={{ fontSize: '0.85em', color: '#888' }}>
          Thông tin được mã hóa DPAPI và lưu cục bộ. Bạn có thể đăng nhập thủ công trong trình duyệt Flow;
          sau đó nhấn <strong>Kiểm tra đăng nhập</strong> để xác nhận Google Flow mở được.
        </p>

        <div className="action-buttons">
          <button className="btn-save" onClick={handleSave}>💾 Lưu thông tin</button>
          <button className="btn-primary" onClick={handleCheckLogin}>
            🔍 Kiểm tra đăng nhập
          </button>
          <button className="btn-danger" onClick={handleClear}>🗑️ Xóa tài khoản</button>
        </div>

        {(loadingMsg || resultMsg) && (
          <div className="status-box">
            {loadingMsg && <p className="loading">{loadingMsg}</p>}
            {resultMsg && <p className={`result ${resultType}`}>{resultMsg}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
