'use client';

import { io, Socket } from 'socket.io-client';

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

let socket: Socket | null = null;

export function getSocket(): Socket {
    if (!socket) {
        socket = io(BACKEND_URL, {
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionAttempts: Infinity,
            reconnectionDelay: 1000,
            reconnectionDelayMax: 5000,
        });
    }
    return socket;
}

export function disconnectSocket() {
    if (socket) {
        socket.disconnect();
        socket = null;
    }
}

// API helpers
const api = {
    async listFiles(path: string = '') {
        const res = await fetch(`${BACKEND_URL}/api/files?path=${encodeURIComponent(path)}`);
        return res.json();
    },
    async readFile(path: string) {
        const res = await fetch(`${BACKEND_URL}/api/file?path=${encodeURIComponent(path)}`);
        return res.json();
    },
    async writeFile(path: string, content: string) {
        const res = await fetch(`${BACKEND_URL}/api/file`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path, content }),
        });
        return res.json();
    },
    async getModelInfo() {
        const res = await fetch(`${BACKEND_URL}/api/model`);
        return res.json();
    },
    async searchFiles(query: string) {
        const res = await fetch(`${BACKEND_URL}/api/search?q=${encodeURIComponent(query)}`);
        return res.json();
    },
    async getWorkspace() {
        const res = await fetch(`${BACKEND_URL}/api/workspace`);
        return res.json();
    },
    async setWorkspace(path: string) {
        const res = await fetch(`${BACKEND_URL}/api/workspace`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ workspace: path }),
        });
        return res.json();
    },
};

export default api;
