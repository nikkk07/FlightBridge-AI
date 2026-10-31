import { createContext, useContext, useEffect, useState } from 'react';
import { api, getToken, setToken } from './api';

const AuthCtx = createContext(null);
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }) {
  const [me, setMe] = useState(null);
  const [state, setState] = useState(getToken() ? 'checking' : 'signed_out'); // checking | signed_out | ready | no_access | not_deployed
  const [error, setError] = useState(null);

  const loadMe = () => {
    setState('checking');
    return api.me().then(
      (m) => { setMe(m); setState('ready'); },
      (e) => {
        setError(e);
        if (e.kind === 'auth') setState('signed_out');
        else if (e.kind === 'forbidden') setState('no_access');
        else if (e.kind === 'not_deployed') setState('not_deployed');
        else setState('error');
      },
    );
  };

  useEffect(() => { if (getToken()) loadMe(); }, []);

  const signIn = async (loginId, password) => {
    const r = await api.login(loginId, password);
    setToken(r.access_token);
    setError(null);
    await loadMe();
  };
  const signOut = () => { setToken(null); setMe(null); setState('signed_out'); };

  return (
    <AuthCtx.Provider value={{ me, state, error, signIn, signOut, retry: loadMe, isAdmin: me?.role === 'admin' }}>
      {children}
    </AuthCtx.Provider>
  );
}
