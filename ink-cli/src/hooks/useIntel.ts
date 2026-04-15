import { useState, useEffect } from 'react';
import type { Socket } from 'socket.io-client';

export interface FeedItem {
  id: string;
  type: 'reading' | 'finding' | 'gap' | 'improvement' | 'round_start' | 'round_done' | 'scores';
  domain?: string;
  text: string;
  impact?: string;
  timestamp: Date;
}

export interface IntelState {
  running: boolean;
  round: number;
  overallScore: number;
  totalImprovements: number;
  scores: Record<string, number>;
  feed: FeedItem[];
}

const FEED_CAP = 20;

function makeFeedItem(
  type: FeedItem['type'],
  text: string,
  domain?: string,
  impact?: string,
): FeedItem {
  return {
    id: crypto.randomUUID(),
    type,
    domain,
    text,
    impact,
    timestamp: new Date(),
  };
}

function appendFeed(prev: FeedItem[], item: FeedItem): FeedItem[] {
  const next = [...prev, item];
  return next.length > FEED_CAP ? next.slice(next.length - FEED_CAP) : next;
}

export function useIntel(socket: Socket | null): IntelState {
  const [running, setRunning] = useState(false);
  const [round, setRound] = useState(0);
  const [overallScore, setOverallScore] = useState(0);
  const [totalImprovements, setTotalImprovements] = useState(0);
  const [scores, setScores] = useState<Record<string, number>>({});
  const [feed, setFeed] = useState<FeedItem[]>([]);

  useEffect(() => {
    if (!socket) return;

    const onStatus = (data: {
      running: boolean;
      round: number;
      overall_score: number;
      total_improvements: number;
    }) => {
      setRunning(data.running);
      setRound(data.round);
      setOverallScore(data.overall_score);
      setTotalImprovements(data.total_improvements);
    };

    const onRoundStart = (data: { round: number; timestamp: string }) => {
      setRound(data.round);
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeedItem('round_start', `Round ${data.round} started`),
        ),
      );
    };

    const onReading = (data: { domain: string; file: string }) => {
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeedItem('reading', `Reading ${data.file}`, data.domain),
        ),
      );
    };

    const onFinding = (data: {
      domain: string;
      file: string;
      patterns_found: number;
      snippets: string[];
    }) => {
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeedItem(
            'finding',
            `Found ${data.patterns_found} pattern${data.patterns_found !== 1 ? 's' : ''} in ${data.file}`,
            data.domain,
          ),
        ),
      );
    };

    const onGap = (data: {
      domain: string;
      gap: string;
      cc_technique: string;
      impact: string;
    }) => {
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeedItem('gap', `GAP(${data.impact}) ${data.gap}`, data.domain, data.impact),
        ),
      );
    };

    const onImprovement = (data: {
      domain: string;
      target_file: string;
      description: string;
    }) => {
      setTotalImprovements(prev => prev + 1);
      setFeed(prev =>
        appendFeed(
          prev,
          makeFeedItem(
            'improvement',
            `Applied -> ${data.target_file}  ${data.description}`,
            data.domain,
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
          makeFeedItem('scores', `Scores updated — overall ${data.overall}%`),
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
            `Round ${data.round} complete — ${data.improvements_this_round} improvement${data.improvements_this_round !== 1 ? 's' : ''} — starting round ${data.round + 1}`,
          ),
        ),
      );
    };

    socket.on('intel:status', onStatus);
    socket.on('intel:round_start', onRoundStart);
    socket.on('intel:reading', onReading);
    socket.on('intel:finding', onFinding);
    socket.on('intel:gap', onGap);
    socket.on('intel:improvement', onImprovement);
    socket.on('intel:scores', onScores);
    socket.on('intel:round_done', onRoundDone);

    return () => {
      socket.off('intel:status', onStatus);
      socket.off('intel:round_start', onRoundStart);
      socket.off('intel:reading', onReading);
      socket.off('intel:finding', onFinding);
      socket.off('intel:gap', onGap);
      socket.off('intel:improvement', onImprovement);
      socket.off('intel:scores', onScores);
      socket.off('intel:round_done', onRoundDone);
    };
  }, [socket]);

  return { running, round, overallScore, totalImprovements, scores, feed };
}
