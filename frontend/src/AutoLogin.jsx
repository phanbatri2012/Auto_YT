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
  const [loadingMsg, setLoadingMsg] = useState('');
  const [resultMsg, setResultMsg] = useState('');

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

  return (
    <div className="auto-login-container">
      <h1 className="hero-title">ChatGPT Auto Login</h1>
      <p className="hero-subtitle">Tự động cấu hình phiên đăng nhập cho ChatGPT để dùng làm công cụ chạy nền.</p>
      
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
          Cấu hình chạy ẩn và Chế độ chơi game nằm trong Settings. Auto Login
          và Open Profile luôn mở trình duyệt vì đây là thao tác do bạn chủ động gọi.
        </p>
        
        <div className="action-buttons">
          <button className="btn-save" onClick={handleSave}>💾 Save Settings</button>
          <button className="btn-primary" onClick={handleAutoLogin}>🚀 Auto Login</button>
          <button className="btn-secondary" onClick={handleOpenProfile}>🌐 Open Profile</button>
          <button className="btn-danger" onClick={handleClear}>🗑️ Clear Account</button>
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
