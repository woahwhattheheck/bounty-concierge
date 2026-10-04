import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet, Alert, TouchableOpacity, ScrollView, Platform, Linking, ActivityIndicator } from 'react-native';
import { useLocalSearchParams, useRouter, Stack } from 'expo-router';
import * as Clipboard from 'expo-clipboard';
import { Copy, Check, ArrowLeft, ArrowUpRight, ArrowDownLeft, ExternalLink, AlertCircle, Clock, CheckCircle, XCircle, RefreshCw } from 'lucide-react-native';
import { useWalletStore } from '../../src/store/walletStore';
import { useAppStore } from '../../src/store/appStore';
import { COLORS, SIZES, RADIUS } from '../../src/constants/theme';
import { Button } from '../../src/components/Button';
import { resolveAddressLabel } from '../../src/utils/contacts';
import { formatAmount } from '../../src/utils/amount';
import { validateTransactionId } from '../../src/utils/validation';
import { getExplorerTxUrl, fetchOperationById } from '../../src/services/stellar';
import type { TransactionDetail } from '../../src/features/transactions/types';

type DeepLinkLoadState = 'idle' | 'loading' | 'loaded' | 'not_found' | 'error' | 'invalid';

export default function TransactionDetailScreen() {
  const router = useRouter();
  const { id } = useLocalSearchParams<{ id: string }>();
  const { transactions, publicKey } = useWalletStore();
  const contacts = useAppStore((state) => state.contacts);
  const { copy, copiedField } = useCopyToClipboard();

  // Deep link state: for fetching from network when not in local store
  const [deepLinkState, setDeepLinkState] = useState<DeepLinkLoadState>('idle');
  const [fetchedTx, setFetchedTx] = useState<TransactionDetail | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  // Validate the ID param
  const validationError = validateTransactionId(id);

  // First, try the in-memory store
  const storeTransaction = transactions.find((tx) => tx.id === id);

  // If not in store and ID is valid, fetch from network (deep link scenario)
  useEffect(() => {
    if (validationError) {
      setDeepLinkState('invalid');
      return;
    }

    // If found in store, no network fetch needed
    if (storeTransaction) {
      setDeepLinkState('loaded');
      setFetchedTx(storeTransaction as TransactionDetail);
      return;
    }

    // Only fetch on initial load or explicit retry (retryCount changes)
    if (deepLinkState === 'loaded') {
      return;
    }

    let cancelled = false;

    const fetchFromNetwork = async () => {
      setDeepLinkState('loading');
      try {
        const record = await fetchOperationById(id!);
        if (cancelled) return;

        if (record) {
          setFetchedTx(record as unknown as TransactionDetail);
          setDeepLinkState('loaded');
        } else {
          setDeepLinkState('not_found');
        }
      } catch (err: any) {
        if (cancelled) return;
        console.error('Deep link transaction fetch failed:', err);
        setDeepLinkState('error');
      }
    };

    fetchFromNetwork();

    return () => {
      cancelled = true;
    };
  }, [id, storeTransaction, validationError, retryCount]);

  // Derive the final transaction to display
  const transaction: TransactionDetail | null = storeTransaction
    ? (storeTransaction as TransactionDetail)
    : fetchedTx;

  const handleRetry = () => {
    setDeepLinkState('idle');
    setFetchedTx(null);
    setRetryCount((c) => c + 1);
  };

  // ── Invalid ID State ──────────────────────────────────────
  if (validationError) {
    return (
      <View style={styles.errorContainer} testID="error-container">
        <AlertCircle color={COLORS.error} size={48} style={{ marginBottom: SIZES.md }} />
        <Text style={styles.errorTitle}>Invalid Transaction Link</Text>
        <Text style={styles.errorText}>{validationError}</Text>
        <Button title="Go Back" onPress={() => router.back()} />
      </View>
    );
  }

  // ── Loading State (deep link network fetch) ───────────────
  if (deepLinkState === 'loading' || (deepLinkState === 'idle' && !validationError && !storeTransaction)) {
    return (
      <View style={styles.errorContainer} testID="loading-container">
        <ActivityIndicator color={COLORS.primary} size="large" />
        <Text style={[styles.errorText, { marginTop: SIZES.md }]}>Loading transaction…</Text>
      </View>
    );
  }

  // ── Not Found State ───────────────────────────────────────
  if (deepLinkState === 'not_found' && !transaction) {
    return (
      <View style={styles.errorContainer} testID="error-container">
        <AlertCircle color={COLORS.warning} size={48} style={{ marginBottom: SIZES.md }} />
        <Text style={styles.errorTitle}>Transaction Not Found</Text>
        <Text style={styles.errorText}>
          This transaction doesn't exist on the network or may belong to a different account.
        </Text>
        <View style={{ gap: SIZES.sm, width: '100%' }}>
          <Button title="Try Again" onPress={handleRetry} />
          <Button title="Go Back" variant="secondary" onPress={() => router.back()} />
        </View>
      </View>
    );
  }

  // ── Network Error State ───────────────────────────────────
  if (deepLinkState === 'error' && !transaction) {
    return (
      <View style={styles.errorContainer} testID="error-container">
        <AlertCircle color={COLORS.error} size={48} style={{ marginBottom: SIZES.md }} />
        <Text style={styles.errorTitle}>Connection Error</Text>
        <Text style={styles.errorText}>
          Unable to load transaction details. Please check your connection and try again.
        </Text>
        <View style={{ gap: SIZES.sm, width: '100%' }}>
          <Button title="Retry" onPress={handleRetry} />
          <Button title="Go Back" variant="secondary" onPress={() => router.back()} />
        </View>
      </View>
    );
  }

  // ── Not Found (store lookup, no deep link fetch attempted) ─
  if (!transaction) {
    return (
      <View style={styles.errorContainer} testID="error-container">
        <Text style={styles.errorText}>Transaction not found</Text>
        <Button title="Go Back" onPress={() => router.back()} />
      </View>
    );
  }

  const tx = transaction;

  const isSent = !!publicKey && tx.from === publicKey;
  const directionLabel = isSent ? 'Sent' : 'Received';
  const amountColor = isSent ? COLORS.textPrimary : COLORS.success;
  const formattedAmount = `${isSent ? '-' : '+'}${tx.amount ? formatAmount(tx.amount) : 'N/A'} ${tx.asset || 'XLM'}`;
  const formattedDate = tx.createdAt 
    ? new Date(tx.createdAt).toLocaleString() 
    : tx.created_at
    ? new Date(tx.created_at).toLocaleString()
    : tx.timestamp 
    ? new Date(tx.timestamp).toLocaleString()
    : 'Unknown date';

  const txHash = tx.hash || tx.transaction_hash || '';
  const senderAddress = tx.from || '';
  const recipientAddress = tx.to || '';
  const memoText = tx.memo || '';
  const memoType = tx.memo_type || '';

  // Status determination
  const isPending = tx.is_pending === true;
  const isFailed = tx.transaction_successful === false;
  const isSuccessful = !isPending && !isFailed;

  const senderLabel = resolveAddressLabel(senderAddress, contacts);
  const recipientLabel = resolveAddressLabel(recipientAddress, contacts);

  // Explorer link
  const explorerUrl = getExplorerTxUrl(txHash);

  const handleCopy = async (text: string, fieldName: string) => {
    if (!text) return;
    const result = await copy(text, fieldName);
    if (!result.ok) {
      Alert.alert('Copy Failed', 'Failed to copy to clipboard. Please try again.');
    }
  };

  const handleOpenExplorer = async () => {
    if (!explorerUrl) return;
    try {
      const canOpen = await Linking.canOpenURL(explorerUrl);
      if (canOpen) {
        await Linking.openURL(explorerUrl);
      } else {
        Alert.alert('Error', 'Unable to open explorer link.');
      }
    } catch (error: any) {
      console.error('Failed to open explorer:', error);
      Alert.alert('Error', 'Failed to open explorer link.');
    }
  };

  const getStatusConfig = () => {
    if (isPending) {
      return {
        icon: <Clock color={COLORS.warning} size={18} />,
        label: 'Pending',
        color: COLORS.warning,
        bgColor: 'rgba(255, 196, 0, 0.1)',
      };
    }
    if (isFailed) {
      return {
        icon: <XCircle color={COLORS.error} size={18} />,
        label: 'Failed',
        color: COLORS.error,
        bgColor: 'rgba(255, 61, 0, 0.1)',
      };
    }
    return {
      icon: <CheckCircle color={COLORS.success} size={18} />,
      label: 'Successful',
      color: COLORS.success,
      bgColor: 'rgba(0, 230, 118, 0.1)',
    };
  };

  const statusConfig = getStatusConfig();

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.contentContainer}>
      <Stack.Screen 
        options={{
          headerShown: true,
          title: 'Transaction Details',
          headerStyle: { backgroundColor: COLORS.background },
          headerTintColor: COLORS.textPrimary,
          headerLeft: () => (
            <TouchableOpacity onPress={() => router.back()} style={styles.backButton}>
              <ArrowLeft color={COLORS.textPrimary} size={24} />
            </TouchableOpacity>
          ),
        }} 
      />

      <View style={styles.heroSection}>
        <View
          style={[
            styles.iconWrapper,
            { backgroundColor: isSent ? 'rgba(255, 61, 0, 0.1)' : 'rgba(0, 230, 118, 0.1)' },
          ]}
        >
          {isSent ? (
            <ArrowUpRight color={COLORS.error} size={32} />
          ) : (
            <ArrowDownLeft color={COLORS.success} size={32} />
          )}
        </View>
        <Text style={[styles.amountText, { color: amountColor }]} testID="detail-amount">
          {formattedAmount}
        </Text>
        <Text style={styles.dateText}>{formattedDate}</Text>
        
        {/* Status Badge */}
        <View style={[styles.statusBadge, { backgroundColor: statusConfig.bgColor }]}>
          {statusConfig.icon}
          <Text style={[styles.statusText, { color: statusConfig.color }]}>
            {statusConfig.label}
          </Text>
        </View>
      </View>

      <View style={styles.detailsCard}>
        {/* Type / Direction */}
        <View style={styles.detailRow}>
          <Text style={styles.rowLabel}>Type</Text>
          <Text style={styles.rowValue}>{directionLabel} XLM</Text>
        </View>

        {/* Status Row with More Details */}
        <View style={styles.detailRow}>
          <Text style={styles.rowLabel}>Status</Text>
          <View style={styles.statusRowValue}>
            {statusConfig.icon}
            <Text style={[styles.rowValue, { color: statusConfig.color, marginLeft: SIZES.xs }]}>
              {statusConfig.label}
            </Text>
          </View>
        </View>

        {/* Memo */}
        {memoText ? (
          <View style={styles.detailRow}>
            <View style={styles.labelWithAction}>
              <Text style={styles.rowLabel}>
                Memo{memoType ? ` (${memoType})` : ''}
              </Text>
              <TouchableOpacity 
                onPress={() => handleCopy(memoText, 'memo')} 
                style={styles.copyBtn}
                testID="copy-memo-btn"
              >
                {copiedField === 'memo' ? (
                  <View style={styles.copiedFeedback}>
                    <Check color={COLORS.success} size={16} />
                    <Text style={styles.copiedText}>Copied</Text>
                  </View>
                ) : (
                  <Copy color={COLORS.primary} size={16} />
                )}
              </TouchableOpacity>
            </View>
            <Text style={styles.memoText} selectable>
              {memoText}
            </Text>
          </View>
        ) : null}

        {/* Transaction Hash */}
        {txHash ? (
          <View style={styles.detailRow}>
            <View style={styles.labelWithAction}>
              <Text style={styles.rowLabel}>Transaction Hash</Text>
              <TouchableOpacity 
                onPress={() => handleCopy(txHash, 'hash')} 
                style={styles.copyBtn}
                testID="copy-hash-btn"
                accessibilityLabel="Copy transaction hash"
                accessibilityRole="button"
              >
                {copiedField === 'hash' ? (
                  <View style={styles.copiedFeedback}>
                    <Check color={COLORS.success} size={16} />
                    <Text style={styles.copiedText}>Copied</Text>
                  </View>
                ) : (
                  <Copy color={COLORS.primary} size={16} />
                )}
              </TouchableOpacity>
            </View>
            <Text style={styles.addressText} selectable numberOfLines={2}>
              {txHash}
            </Text>
            <Text style={styles.hashHelper}>
              This code identifies your payment. Copy it to share or look it up.
            </Text>
          </View>
        ) : null}

        {/* Sender Address */}
        {senderAddress ? (
          <View style={styles.detailRow}>
            <View style={styles.labelWithAction}>
              <Text style={styles.rowLabel}>
                Sender (From){senderLabel.isContact ? ` · ${senderLabel.label}` : ''}
              </Text>
              <TouchableOpacity 
                onPress={() => handleCopy(senderAddress, 'sender')} 
                style={styles.copyBtn}
                testID="copy-sender-btn"
              >
                {copiedField === 'sender' ? (
                  <View style={styles.copiedFeedback}>
                    <Check color={COLORS.success} size={16} />
                    <Text style={styles.copiedText}>Copied</Text>
                  </View>
                ) : (
                  <Copy color={COLORS.primary} size={16} />
                )}
              </TouchableOpacity>
            </View>
            <Text style={styles.addressText} selectable numberOfLines={2}>
              {senderAddress}
            </Text>
          </View>
        ) : null}

        {/* Recipient Address */}
        {recipientAddress ? (
          <View style={styles.detailRow}>
            <View style={styles.labelWithAction}>
              <Text style={styles.rowLabel}>
                Recipient (To){recipientLabel.isContact ? ` · ${recipientLabel.label}` : ''}
              </Text>
              <TouchableOpacity 
                onPress={() => handleCopy(recipientAddress, 'recipient')} 
                style={styles.copyBtn}
                testID="copy-recipient-btn"
              >
                {copiedField === 'recipient' ? (
                  <View style={styles.copiedFeedback}>
                    <Check color={COLORS.success} size={16} />
                    <Text style={styles.copiedText}>Copied</Text>
                  </View>
                ) : (
                  <Copy color={COLORS.primary} size={16} />
                )}
              </TouchableOpacity>
            </View>
            <Text style={styles.addressText} selectable numberOfLines={2}>
              {recipientAddress}
            </Text>
          </View>
        ) : null}
      </View>

      {/* Explorer Link Section */}
      {explorerUrl ? (
        <View style={styles.explorerSection}>
          <TouchableOpacity 
            style={styles.explorerButton}
            onPress={handleOpenExplorer}
            testID="explorer-link-btn"
          >
            <ExternalLink color={COLORS.primary} size={20} />
            <Text style={styles.explorerButtonText}>View on Stellar Explorer</Text>
          </TouchableOpacity>
          <Text style={styles.explorerHint}>
            View full transaction details and network information
          </Text>
        </View>
      ) : txHash ? (
        <View style={styles.explorerSection}>
          <View style={styles.explorerUnavailable}>
            <AlertCircle color={COLORS.textMuted} size={16} />
            <Text style={styles.explorerUnavailableText}>
              Explorer not available for this network
            </Text>
          </View>
        </View>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.background,
  },
  contentContainer: {
    padding: SIZES.lg,
    paddingBottom: SIZES.xxl,
  },
  backButton: {
    marginRight: SIZES.md,
  },
  errorContainer: {
    flex: 1,
    backgroundColor: COLORS.background,
    justifyContent: 'center',
    alignItems: 'center',
    padding: SIZES.xl,
  },
  errorTitle: {
    color: COLORS.textPrimary,
    fontSize: 20,
    fontWeight: 'bold',
    marginBottom: SIZES.sm,
    textAlign: 'center',
  },
  errorText: {
    color: COLORS.textSecondary,
    fontSize: 16,
    textAlign: 'center',
    marginBottom: SIZES.xl,
    lineHeight: 24,
  },
  heroSection: {
    alignItems: 'center',
    marginVertical: SIZES.xl,
  },
  iconWrapper: {
    width: 64,
    height: 64,
    borderRadius: 32,
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: SIZES.md,
  },
  amountText: {
    fontSize: 28,
    fontWeight: 'bold',
    marginBottom: SIZES.xs,
  },
  dateText: {
    color: COLORS.textMuted,
    fontSize: 14,
  },
  detailsCard: {
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.lg,
    padding: SIZES.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  detailRow: {
    marginBottom: SIZES.lg,
    borderBottomWidth: 1,
    borderBottomColor: COLORS.border,
    paddingBottom: SIZES.md,
  },
  rowLabel: {
    color: COLORS.textSecondary,
    fontSize: 14,
    fontWeight: '500',
  },
  labelWithAction: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: SIZES.xs,
  },
  rowValue: {
    color: COLORS.textPrimary,
    fontSize: 16,
    fontWeight: '600',
    marginTop: SIZES.xs,
  },
  addressText: {
    color: COLORS.textPrimary,
    fontSize: 13,
    fontFamily: Platform.OS === 'ios' ? 'Courier' : 'monospace',
    lineHeight: 18,
    marginTop: SIZES.xs,
  },
  copyBtn: {
    padding: SIZES.xs,
  },
  hashHelper: {
    color: COLORS.textMuted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: SIZES.xs,
  },
  copiedFeedback: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
  },
  copiedText: {
    color: COLORS.success,
    fontSize: 12,
    fontWeight: '500',
  },
  statusBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: SIZES.xs,
    paddingHorizontal: SIZES.md,
    paddingVertical: SIZES.sm,
    borderRadius: RADIUS.round,
    marginTop: SIZES.md,
  },
  statusText: {
    fontSize: 14,
    fontWeight: '600',
  },
  statusRowValue: {
    flexDirection: 'row',
    alignItems: 'center',
    marginTop: SIZES.xs,
  },
  memoText: {
    color: COLORS.textPrimary,
    fontSize: 14,
    lineHeight: 20,
    marginTop: SIZES.xs,
  },
  explorerSection: {
    marginTop: SIZES.lg,
    backgroundColor: COLORS.surface,
    borderRadius: RADIUS.lg,
    padding: SIZES.lg,
    borderWidth: 1,
    borderColor: COLORS.border,
  },
  explorerButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: SIZES.sm,
    backgroundColor: 'rgba(0, 229, 255, 0.1)',
    paddingVertical: SIZES.md,
    paddingHorizontal: SIZES.lg,
    borderRadius: RADIUS.md,
    borderWidth: 1,
    borderColor: COLORS.primary,
  },
  explorerButtonText: {
    color: COLORS.primary,
    fontSize: 15,
    fontWeight: '600',
  },
  explorerHint: {
    color: COLORS.textMuted,
    fontSize: 12,
    textAlign: 'center',
    marginTop: SIZES.sm,
  },
  explorerUnavailable: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: SIZES.xs,
    paddingVertical: SIZES.sm,
  },
  explorerUnavailableText: {
    color: COLORS.textMuted,
    fontSize: 13,
  },
});
