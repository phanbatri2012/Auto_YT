import { useState, useEffect } from 'react';
import './AutoLogin.css';

export default function AutoLogin() {
  const [account, setAccount] = useState({
    email: '',
    password: '',
    totp_secret: '',
    password_configured: false,
    totp_configured: false,
  });
  const [browserStatus, setBrowserStatus] = useState(null);
  const [loadingMsg, setLoadingMsg] = useState('');
  const [resultMsg, setResultMsg] = useState('');

  const fetchBrowserStatus = async () => {
    try {
      const res = await fetch('http://127.0.0.1:8080/api/chatgpt-browser-service');
      if (res.ok) {
        const data = await res.json();
        setBrowserStatus(data);
      }
    } catch (err) {
      console.error("Lỗi khi lấy trạng thái trình duyệt ChatGPT", err);
    }
  };

  useEffect(() => {
    fetch('http://127.0.0.1:8080/api/account')
      .then(res => res.json())
      .then(data => setAccount(prev => ({
        ...prev,
        email: data.email || '',
        password: '',
        totp_secret: '',
        password_configured: Boolean(data.password_configured),
        totp_configured: Boolean(data.totp_configured),
      })))
      .catch(err => console.error("Lỗi khi lấy account", err));

    fetchBrowserStatus();
    const interval = setInterval(fetchBrowserStatus, 3000);
    return () => clearInterval(interval);
  }, []);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    setAccount(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value
    }));
  };

  const handleSave = async () => {
    try {
      setLoadingMsg('Đang lưu cấu hình...');
      const response = await fetch('http://127.0.0.1:8080/api/account', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: account.email,
          password: account.password,
          totp_secret: account.totp_secret,
        })
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setAccount(prev => ({
        ...prev,
        password: '',
        totp_secret: '',
        password_configured: prev.password_configured || Boolean(prev.password),
        totp_configured: prev.totp_configured || Boolean(prev.totp_secret),
      }));
      setResultMsg('✅ Đã lưu cấu hình thành công!');
    } catch {
      setResultMsg('❌ Lỗi khi lưu cấu hình.');
    } finally {
      setLoadingMsg('');
    }
  };

  const handleClear = async () => {
    if (!confirm("Bạn có chắc chắn muốn xóa toàn bộ thông tin đăng nhập và Profile trình duyệt?")) return;
    try {
      setLoadingMsg('Đang xóa thông tin...');
      await fetch('http://127.0.0.1:8080/api/clear-account', { method: 'POST' });
      setAccount({
        email: '',
        password: '',
        totp_secret: '',
        password_configured: false,
        totp_configured: false,
      });
      setResultMsg('✅ Đã xóa thông tin thành công!');
    } catch {
      setResultMsg('❌ Lỗi khi xóa thông tin.');
    } finally {
      setLoadingMsg('');
    }
  };

  const handleOpenProfile = async () => {
    try {
      setLoadingMsg('Đang mở trình duyệt...');
      await fetch('http://127.0.0.1:8080/api/open-profile', { method: 'POST' });
      setResultMsg('🌐 Đang mở cửa sổ Chrome (Sẽ tự đóng sau 10 phút hoặc khi bạn tắt).');
    } catch {
      setResultMsg('❌ Lỗi khi mở trình duyệt.');
    } finally {
      setLoadingMsg('');
    }
  };

  const handleAutoLogin = async () => {
    try {
      setLoadingMsg('🤖 Đang chạy kịch bản Auto Login... (Sẽ tốn 30s - 1 phút, vui lòng chờ)');
      setResultMsg('');
      const response = await fetch('http://127.0.0.1:8080/api/login-chatgpt', { method: 'POST' });
      const data = await response.json();
      if (data.success) {
        setResultMsg(`✅ Auto Login thành công! ${data.message || ''}`);
      } else {
        setResultMsg(`❌ Lỗi Auto Login: ${data.error || data.message}`);
      }
    } catch {
      setResultMsg('❌ Lỗi kết nối tới Backend.');
    } finally {
      setLoadingMsg('');
    }
  };

  const handleBrowserAction = async (action) => {
    const act = action || (browserStatus?.window_visible ? 'hide' : 'show');
    const labels = {
      show: 'Đang hiển thị trình duyệt ChatGPT...',
      hide: 'Đang ẩn trình duyệt ChatGPT...',
      start: 'Đang khởi động trình duyệt ChatGPT...',
      stop: 'Đang dừng trình duyệt ChatGPT...'
    };
    try {
      setLoadingMsg(labels[act] || 'Đang xử lý...');
      setResultMsg('');
      const response = await fetch(`http://127.0.0.1:8080/api/chatgpt-browser-service/${act}`, {
        method: 'POST'
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || data.message || 'Lỗi thao tác trình duyệt');
      }
      setBrowserStatus(data);
      const messages = {
        show: '👁 Trình duyệt ChatGPT đang hiển thị trên màn hình.',
        hide: '🙈 Trình duyệt ChatGPT đã được ẩn vào nền.',
        start: '🌐 Trình duyệt ChatGPT đã được khởi động.',
        stop: '⏹️ Trình duyệt ChatGPT đã dừng.'
      };
      setResultMsg(messages[act] || data.message || 'Thành công.');
    } catch (err) {
      setResultMsg(`❌ ${err.message}`);
    } finally {
      setLoadingMsg('');
    }
  };

  const isConnected = Boolean(browserStatus?.connected);
  const isAlive = Boolean(browserStatus?.process_alive);

  return (
    <div className="auto-login-container">
      <h1 className="hero-title">ChatGPT Auto Login</h1>
      <p className="hero-subtitle">Hệ thống tự đăng nhập lại khi Playwright xác nhận phiên ChatGPT hết hạn.</p>
      
      {/* Browser Status */}
      <div className="login-panel" style={{ marginBottom: '16px' }}>
        <h3 style={{ margin: '0 0 12px', color: '#ccc', fontSize: '1em' }}>Trạng thái Trình duyệt ChatGPT</h3>
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
              onClick={() => handleBrowserAction(browserStatus?.window_visible ? 'hide' : 'show')}
            >
              {browserStatus?.window_visible ? '🙈 Ẩn trình duyệt' : '👁 Hiện trình duyệt'}
            </button>
            {!isAlive && (
              <button
                className="btn-primary"
                style={{ width: 'auto', padding: '8px 16px' }}
                onClick={() => handleBrowserAction('show')}
              >
                ▶ Khởi động & Mở
              </button>
            )}
            {isAlive && (
              <button
                className="btn-secondary"
                style={{ width: 'auto', padding: '8px 16px' }}
                onClick={() => handleBrowserAction('stop')}
              >
                ⏹ Dừng
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="login-panel">
        <div className="form-group">
          <label>Email OpenAI:</label>
          <input 
            type="email" 
            name="email" 
            value={account.email} 
            onChange={handleChange} 
            placeholder="example@gmail.com" 
          />
        </div>
        
        <div className="form-group">
          <label>Mật khẩu:</label>
          <input 
            type="password" 
            name="password" 
            value={account.password} 
            onChange={handleChange} 
            placeholder={account.password_configured ? 'Đã lưu — để trống để giữ nguyên' : 'Nhập mật khẩu...'}
          />
        </div>

        <div className="form-group">
          <label>Mã bảo mật 2FA (TOTP Secret) - Tùy chọn:</label>
          <input 
            type="password"
            name="totp_secret" 
            value={account.totp_secret} 
            onChange={handleChange} 
            placeholder={account.totp_configured ? 'Đã lưu — để trống để giữ nguyên' : 'ABC123XYZ...'}
          />
        </div>

        <p className="help-text" style={{fontSize: '0.85em', color: '#888'}}>
          Job sẽ tự chạy Auto Login một lần khi phiên hết hạn. Nút Auto Login dùng
          để chạy thủ công; Open Profile dành cho CAPTCHA, xác minh thiết bị hoặc MFA
          chưa cấu hình. Cả hai thao tác thủ công đều mở trình duyệt.
        </p>
        
        <div className="action-buttons">
          <button className="btn-save" onClick={handleSave}>💾 Save Settings</button>
          <button className="btn-primary" onClick={handleAutoLogin}>🚀 Auto Login</button>
          <button
            className="btn-secondary"
            onClick={() => handleBrowserAction(browserStatus?.window_visible ? 'hide' : 'show')}
            disabled={!isConnected}
          >
            {browserStatus?.window_visible ? '🙈 Ẩn trình duyệt' : '👁 Hiện trình duyệt'}
          </button>
          <button className="btn-secondary" onClick={handleOpenProfile}>🌐 Open Profile</button>
          <button className="btn-danger" style={{ gridColumn: 'span 2' }} onClick={handleClear}>🗑️ Clear Account</button>
        </div>
        
        {(loadingMsg || resultMsg) && (
          <div className="status-box">
            {loadingMsg && <p className="loading">{loadingMsg}</p>}
            {resultMsg && <p className="result">{resultMsg}</p>}
          </div>
        )}
      </div>
    </div>
  );
}
