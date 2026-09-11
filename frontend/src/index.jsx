import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
// Side effect only, every native date box in the app opens its calendar on a
// click anywhere in the field and on keyboard arrival. See lib/datePickers.js.
import './lib/datePickers';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
