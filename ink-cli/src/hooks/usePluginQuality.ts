import { useState, useEffect } from 'react';
import type { Socket } from 'socket.io-client';

export type PQSeverity = 'high' | 'medium' | 'low';

export interface PQFeedItem {
  id: string;
  type:
    | 'scanning'
    | 'finding'
    | 'issue'
    | 'round_start'
    | 'round_done'
    | 'scores'
    | 'converged';
  domain?: string;
  text: string;
  severity?: PQSeverity;
  timestamp: Date;
}

export interface PluginQualityState {
  running: boolean;
  converged: boolean;
  round: number;
  overallScore: number;
  totalIssues: number;
  scores: Record<string, number>;
  feed: PQFeedItem[];
}

const FEED_CAP = 20;

function makeFeed(
  type: PQFeedItem['type'],
  text: string,
  domain?: string,
  severity?: PQSeverity,
): PQFeedItem {
  return { id: crypto.randomUUID(), type, domain, text, severity, timestamp: new Date() };
}

function appendFeed(prev: PQFeedItem[], item: PQFeedItem): PQFeedItem[] {
  const next = [...prev, item];
  return next.length > FEED_CAP ? next.slice(next.length - FEED_CAP) : next;
}

export function usePluginQuality(socket: Socket | null): PluginQualityState {
  const [running, setRunning] = useState(false);
  const [converged, setConverged] = useState(false);
  const [round, setRound] = useState(0);
  const [overallScore, setOverallScore] = useState(0);
  const [totalIssues, setTotalIssues] = useState(0);
  const [scores, setScores] = useState<Record<string, number>>({});
  const [feed, setFeed] = useState<PQFeedItem[]>([]);

  useEffect(() => {
    if (!socket) return;

    const onStatus = (data: {
      running: boolean;
      converged: boolean;
      round: number;
      overall_score: number;
      total_issues: number;
      scores: Record<string, number>;
    }) => {
      setRunning(data.running);
      setConverged(data.converged);
      setRound(data.round);
      setOverallScore(data.overall_score);
      setTotalIssues(data.total_issues);
      setScores(data.scores ?? {});
    };

    const onRoundStart = (data: { round: number }) => {
      setRound(data.round);
      setFeed(prev => appendFeed(prev, makeFeed('round_start', `Round ${data.round} started`)));
    };

    const onScanning = (data: { domain: string }) => {
      setFeed(prev =>
        appendFeed(prev, makeFeed('scanning', `Scanning ${data.domain}`, data.domain)),
      );
    };

    const onFinding = (data: {
      domain: string;
      issues_found: number;
      severity_breakdown: { high: number; medium: number; low: number };
    }) => {
      const b = data.severity_breakdown ?? { high: 0, medium: 0, low: 0 };
      setTotalIssues(prev => prev + data.issues_found);
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeed(
            'finding',
            `${data.issues_found} issue${data.issues_found !== 1 ? 's' : ''}  H:${b.high} M:${b.medium} L:${b.low}`,
            data.domain,
          ),
        ),
      );
    };

    const onIssue = (data: {
      domain: string;
      file: string;
      line: number;
      severity: PQSeverity;
      message: string;
      rule_id: string;
    }) => {
      const loc = data.line > 0 ? `${data.file}:${data.line}` : data.file;
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeed(
            'issue',
            `[${data.rule_id}] ${data.message} @ ${loc}`,
            data.domain,
            data.severity,
          ),
        ),
      );
    };

    const onScores = (data: {
      round: number;
      scores: Record<string, number>;
      overall: number;
    }) => {
      setScores(data.scores);
      setOverallScore(data.overall);
      setFeed(prev =>
        appendFeed(prev, makeFeed('scores', `Scores updated — overall ${data.overall}/100`)),
      );
    };

    const onRoundDone = (data: { round: number; scores: Record<string, number> }) => {
      setScores(data.scores);
      setFeed(prev =>
        appendFeed(prev, makeFeed('round_done', `Round ${data.round} complete`)),
      );
    };

    const onConverged = (data: { round: number; final_score: number; message: string }) => {
      setConverged(true);
      setRunning(false);
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeed('converged', data.message || `Converged at ${data.final_score}/100`),
        ),
      );
    };

    socket.on('plugin_quality:status', onStatus);
    socket.on('plugin_quality:round_start', onRoundStart);
    socket.on('plugin_quality:scanning', onScanning);
    socket.on('plugin_quality:finding', onFinding);
    socket.on('plugin_quality:issue', onIssue);
    socket.on('plugin_quality:scores', onScores);
    socket.on('plugin_quality:round_done', onRoundDone);
    socket.on('plugin_quality:converged', onConverged);

    return () => {
      socket.off('plugin_quality:status', onStatus);
      socket.off('plugin_quality:round_start', onRoundStart);
      socket.off('plugin_quality:scanning', onScanning);
      socket.off('plugin_quality:finding', onFinding);
      socket.off('plugin_quality:issue', onIssue);
      socket.off('plugin_quality:scores', onScores);
      socket.off('plugin_quality:round_done', onRoundDone);
      socket.off('plugin_quality:converged', onConverged);
    };
  }, [socket]);

  return { running, converged, round, overallScore, totalIssues, scores, feed };
}
