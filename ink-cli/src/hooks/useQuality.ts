import { useState, useEffect } from 'react';
import type { Socket } from 'socket.io-client';

export type QualitySeverity = 'high' | 'medium' | 'low';

export interface QualityFeedItem {
  id: string;
  type:
    | 'scanning'
    | 'finding'
    | 'issue'
    | 'improvement'
    | 'round_start'
    | 'round_done'
    | 'scores'
    | 'converged';
  domain?: string;
  text: string;
  severity?: QualitySeverity;
  autoApplied?: boolean;
  timestamp: Date;
}

export interface QualityState {
  running: boolean;
  converged: boolean;
  round: number;
  overallScore: number;
  totalImprovements: number;
  scores: Record<string, number>;
  feed: QualityFeedItem[];
}

const FEED_CAP = 20;

function makeFeedItem(
  type: QualityFeedItem['type'],
  text: string,
  domain?: string,
  severity?: QualitySeverity,
  autoApplied?: boolean,
): QualityFeedItem {
  return {
    id: crypto.randomUUID(),
    type,
    domain,
    text,
    severity,
    autoApplied,
    timestamp: new Date(),
  };
}

function appendFeed(prev: QualityFeedItem[], item: QualityFeedItem): QualityFeedItem[] {
  const next = [...prev, item];
  return next.length > FEED_CAP ? next.slice(next.length - FEED_CAP) : next;
}

export function useQuality(socket: Socket | null): QualityState {
  const [running, setRunning] = useState(false);
  const [converged, setConverged] = useState(false);
  const [round, setRound] = useState(0);
  const [overallScore, setOverallScore] = useState(0);
  const [totalImprovements, setTotalImprovements] = useState(0);
  const [scores, setScores] = useState<Record<string, number>>({});
  const [feed, setFeed] = useState<QualityFeedItem[]>([]);

  useEffect(() => {
    if (!socket) return;

    const onStatus = (data: {
      running: boolean;
      converged: boolean;
      round: number;
      overall_score: number;
      total_improvements: number;
    }) => {
      setRunning(data.running);
      setConverged(data.converged);
      setRound(data.round);
      setOverallScore(data.overall_score);
      setTotalImprovements(data.total_improvements);
    };

    const onRoundStart = (data: { round: number }) => {
      setRound(data.round);
      setFeed(prev =>
        appendFeed(prev, makeFeedItem('round_start', `Round ${data.round} started`)),
      );
    };

    const onScanning = (data: { domain: string; tool: string }) => {
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeedItem('scanning', `Scanning with ${data.tool}`, data.domain),
        ),
      );
    };

    const onFinding = (data: {
      domain: string;
      issues_found: number;
      severity_breakdown: { high: number; medium: number; low: number };
    }) => {
      const b = data.severity_breakdown || { high: 0, medium: 0, low: 0 };
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeedItem(
            'finding',
            `Found ${data.issues_found} issue${data.issues_found !== 1 ? 's' : ''}  H:${b.high} M:${b.medium} L:${b.low}`,
            data.domain,
          ),
        ),
      );
    };

    const onIssue = (data: {
      domain: string;
      file: string;
      line: number;
      severity: QualitySeverity;
      message: string;
      rule_id: string;
    }) => {
      const loc = data.line > 0 ? `${data.file}:${data.line}` : data.file;
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeedItem(
            'issue',
            `[${data.rule_id}] ${data.message} @ ${loc}`,
            data.domain,
            data.severity,
          ),
        ),
      );
    };

    const onImprovement = (data: {
      domain: string;
      target_file: string;
      description: string;
      auto_applied: boolean;
    }) => {
      setTotalImprovements(prev => prev + 1);
      const prefix = data.auto_applied ? 'AUTO-FIX' : 'SUGGEST';
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeedItem(
            'improvement',
            `${prefix} -> ${data.target_file}  ${data.description}`,
            data.domain,
            undefined,
            data.auto_applied,
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
        appendFeed(
          prev,
          makeFeedItem('scores', `Scores updated — overall ${data.overall}/100`),
        ),
      );
    };

    const onRoundDone = (data: {
      round: number;
      improvements_this_round: number;
      scores: Record<string, number>;
    }) => {
      setScores(data.scores);
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeedItem(
            'round_done',
            `Round ${data.round} complete — ${data.improvements_this_round} action${data.improvements_this_round !== 1 ? 's' : ''}`,
          ),
        ),
      );
    };

    const onConverged = (data: {
      round: number;
      final_score: number;
      message: string;
    }) => {
      setConverged(true);
      setRunning(false);
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeedItem('converged', data.message || `Converged at ${data.final_score}/100`),
        ),
      );
    };

    socket.on('quality:status', onStatus);
    socket.on('quality:round_start', onRoundStart);
    socket.on('quality:scanning', onScanning);
    socket.on('quality:finding', onFinding);
    socket.on('quality:issue', onIssue);
    socket.on('quality:improvement', onImprovement);
    socket.on('quality:scores', onScores);
    socket.on('quality:round_done', onRoundDone);
    socket.on('quality:converged', onConverged);

    return () => {
      socket.off('quality:status', onStatus);
      socket.off('quality:round_start', onRoundStart);
      socket.off('quality:scanning', onScanning);
      socket.off('quality:finding', onFinding);
      socket.off('quality:issue', onIssue);
      socket.off('quality:improvement', onImprovement);
      socket.off('quality:scores', onScores);
      socket.off('quality:round_done', onRoundDone);
      socket.off('quality:converged', onConverged);
    };
  }, [socket]);

  return {
    running,
    converged,
    round,
    overallScore,
    totalImprovements,
    scores,
    feed,
  };
}
