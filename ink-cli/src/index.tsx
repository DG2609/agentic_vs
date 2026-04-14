#!/usr/bin/env node
import React from 'react';
import { render } from 'ink';
import App from './App.js';

// Handle uncaught errors gracefully
process.on('uncaughtException', (err) => {
  console.error('Fatal error:', err.message);
  process.exit(1);
});

process.on('unhandledRejection', (reason) => {
  console.error('Unhandled rejection:', reason);
  process.exit(1);
});

const { waitUntilExit } = render(React.createElement(App), {
  exitOnCtrlC: false, // We handle Ctrl+C ourselves
});

waitUntilExit().then(() => {
  process.exit(0);
}).catch(() => {
  process.exit(1);
});
