-- Conditional cohort of visible trial records, NOT service-wide conversion.
-- Loaded CSV tables retain source field names. epoch() normalizes aware timestamps;
-- panel_epoch() interprets naive panel timestamps using params.panel_offset_hours.
-- params: cut_epoch, panel_offset_hours, payment_clock ('created_at'/'updated_at').
CREATE TEMP VIEW first_trial AS
SELECT * FROM (
  SELECT s.*, epoch(created_at) AS trial_at,
         ROW_NUMBER() OVER (PARTITION BY user ORDER BY epoch(created_at), subscription) AS rn
  FROM subscriptions s WHERE is_trial='t' AND user<>''
) WHERE rn=1;

CREATE TEMP VIEW paid AS
SELECT user, epoch(CASE WHEN p.payment_clock='updated_at' THEN updated_at ELSE created_at END) AS paid_at
FROM transactions CROSS JOIN params p
WHERE status='COMPLETED' AND is_test='f' AND CAST(final_amount AS REAL)>0;

CREATE TEMP VIEW cohort AS
SELECT t.*, c.panel_user AS linked_panel,
       panel_epoch(c.first_connected_at) AS connected_at,
       (SELECT MIN(paid_at) FROM paid p WHERE p.user=t.user AND paid_at>=trial_at
           AND paid_at<trial_at+604800 AND paid_at<cfg.cut_epoch) AS paid_7d_at,
       (SELECT MIN(paid_at) FROM paid p WHERE p.user=t.user AND paid_at>=trial_at
           AND paid_at<trial_at+86400 AND paid_at<cfg.cut_epoch) AS paid_24h_at
FROM first_trial t CROSS JOIN params cfg
LEFT JOIN connections c ON c.panel_user=t.panel_user
WHERE trial_at+604800<=cfg.cut_epoch
  AND NOT EXISTS (SELECT 1 FROM paid p WHERE p.user=t.user AND paid_at<trial_at);

-- Snapshot links used only as a suppression check before an assistance message.
-- Include all known panels, including subscription links absent from connections.user.
CREATE TEMP VIEW all_panel_links AS
SELECT user,panel_user FROM subscriptions WHERE user<>'' AND panel_user<>''
UNION SELECT user,panel_user FROM connections WHERE user<>'' AND panel_user<>'';

CREATE TEMP VIEW help_candidates AS
SELECT c.*,
  EXISTS (SELECT 1 FROM all_panel_links l JOIN connections other ON other.panel_user=l.panel_user
    WHERE l.user=c.user AND l.panel_user<>c.panel_user
      AND panel_epoch(other.first_connected_at)<c.trial_at+86400) AS connected_other_panel_by_24h
FROM cohort c WHERE linked_panel IS NOT NULL
  AND (connected_at IS NULL OR connected_at>=trial_at+86400) AND paid_24h_at IS NULL;

CREATE TEMP TABLE metrics AS
SELECT 'visible_trial_users' AS metric, COUNT(*) AS value FROM first_trial
UNION ALL SELECT 'immature_trial_users', COUNT(*) FROM first_trial CROSS JOIN params
 WHERE trial_at+604800>cut_epoch
UNION ALL SELECT 'mature_trial_users_before_exclusion', COUNT(*) FROM first_trial CROSS JOIN params
 WHERE trial_at+604800<=cut_epoch
UNION ALL SELECT 'excluded_prior_payers', COUNT(*) FROM first_trial t CROSS JOIN params
 WHERE trial_at+604800<=cut_epoch AND EXISTS (SELECT 1 FROM paid p WHERE p.user=t.user AND paid_at<trial_at)
UNION ALL SELECT 'cohort_users', COUNT(*) FROM cohort
UNION ALL SELECT 'linked_panel_users', COUNT(*) FROM cohort WHERE linked_panel IS NOT NULL
UNION ALL SELECT 'unknown_panel_users', COUNT(*) FROM cohort WHERE linked_panel IS NULL
UNION ALL SELECT 'linked_null_first_connection', COUNT(*) FROM cohort WHERE linked_panel IS NOT NULL AND connected_at IS NULL
UNION ALL SELECT 'connection_before_trial', COUNT(*) FROM cohort WHERE connected_at<trial_at
UNION ALL SELECT 'connected_7d', COUNT(*) FROM cohort WHERE connected_at>=trial_at AND connected_at<trial_at+604800
UNION ALL SELECT 'connected_24h', COUNT(*) FROM cohort WHERE connected_at>=trial_at AND connected_at<trial_at+86400
UNION ALL SELECT 'paid_7d', COUNT(*) FROM cohort WHERE paid_7d_at IS NOT NULL
UNION ALL SELECT 'connected_then_paid_7d', COUNT(*) FROM cohort WHERE connected_at>=trial_at AND connected_at<trial_at+604800 AND paid_7d_at>=connected_at
UNION ALL SELECT 'paid_before_first_connection_7d', COUNT(*) FROM cohort WHERE paid_7d_at IS NOT NULL AND connected_at>paid_7d_at
UNION ALL SELECT 'paid_without_valid_connection_7d', COUNT(*) FROM cohort WHERE paid_7d_at IS NOT NULL AND (connected_at IS NULL OR connected_at<trial_at OR connected_at>=trial_at+604800)
UNION ALL SELECT 'payment_category_overlap', COUNT(*) FROM cohort WHERE paid_7d_at IS NOT NULL AND connected_at>paid_7d_at AND (connected_at IS NULL OR connected_at<trial_at OR connected_at>=trial_at+604800)
UNION ALL SELECT 'help_24h_candidates', COUNT(*) FROM cohort WHERE linked_panel IS NOT NULL AND (connected_at IS NULL OR connected_at>=trial_at+86400) AND paid_24h_at IS NULL
UNION ALL SELECT 'help_connected_other_panel_by_24h', COUNT(*) FROM help_candidates WHERE connected_other_panel_by_24h
UNION ALL SELECT 'help_after_all_panels_suppression', COUNT(*) FROM help_candidates WHERE NOT connected_other_panel_by_24h
UNION ALL SELECT 'help_candidates_later_connect_7d', COUNT(*) FROM cohort WHERE linked_panel IS NOT NULL AND (connected_at IS NULL OR connected_at>=trial_at+86400) AND paid_24h_at IS NULL AND connected_at<trial_at+604800
UNION ALL SELECT 'help_candidates_paid_7d', COUNT(*) FROM cohort WHERE linked_panel IS NOT NULL AND (connected_at IS NULL OR connected_at>=trial_at+86400) AND paid_24h_at IS NULL AND paid_7d_at IS NOT NULL
UNION ALL SELECT 'median_minutes_to_connection_7d', AVG(minutes) FROM (
  SELECT (connected_at-trial_at)/60.0 AS minutes,
    ROW_NUMBER() OVER (ORDER BY connected_at-trial_at) AS pos,
    COUNT(*) OVER () AS n
  FROM cohort WHERE connected_at>=trial_at AND connected_at<trial_at+604800
) WHERE pos IN ((n+1)/2, (n+2)/2);
