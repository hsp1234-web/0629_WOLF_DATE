# -*- coding: utf-8 -*-
"""
計算模組 (calculator.py)

功能：
- 計算衍生指標 (利差, SOFR 偏差, 持有/準備金比率等)
- 計算各成分的滾動百分位排名
- 計算加權壓力指數 (原始及平滑)
- (可選) 計算 MACD 動能指標
"""

import pandas as pd
import numpy as np

# 全局性的註解，說明此模組的用途和主要提供的函式
# 後續將根據 `一級交易pro.py` 的 Cell 8 內容填充具體函式實現

def calculate_derived_indicators(merged_df: pd.DataFrame) -> pd.DataFrame:
    """
    計算基礎的衍生金融指標。

    Args:
        merged_df (pd.DataFrame): 包含已合併數據源的 DataFrame。
                                  預期欄位包括 'SOFR', 'DGS10', 'DGS2',
                                  'Total_Gross_Positions_Millions', 'Reserves'。

    Returns:
        pd.DataFrame: 包含新增衍生指標欄位的 DataFrame。
                      新增欄位可能包括 'Spread_10Y2Y', 'SOFR_MA60',
                      'SOFR_Dev', 'Pos_Res_Ratio'。
    """
    print("正在計算衍生指標...")
    df = merged_df.copy()

    # 確保計算所需的列為數值類型
    cols_to_ensure_numeric = [
        'SOFR', 'DGS10', 'DGS2',
        'Total_Gross_Positions_Millions', 'Reserves'
    ]
    for col in cols_to_ensure_numeric:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        else:
            print(f"    - 警告：衍生指標計算缺少欄位 {col}，將以 NaN 填充。")
            df[col] = np.nan

    # 計算利差 (10Y - 2Y)
    if 'DGS10' in df.columns and 'DGS2' in df.columns:
        df['Spread_10Y2Y'] = df['DGS10'] - df['DGS2']
        print("    - 利差 (Spread_10Y2Y) 計算完成。")
    else:
        df['Spread_10Y2Y'] = np.nan
        print("    - 警告：未能計算利差 (缺少 DGS10 或 DGS2 數據)。")

    # 計算 SOFR 與 60 日移動平均的偏差
    if 'SOFR' in df.columns and df['SOFR'].notna().any():
        min_periods_ma = 30
        if len(df['SOFR'].dropna()) >= min_periods_ma:
            df['SOFR_MA60'] = df['SOFR'].rolling(window=60, min_periods=min_periods_ma).mean()
            df['SOFR_Dev'] = df['SOFR'] - df['SOFR_MA60']
            print("    - SOFR 60日均線和偏差 (SOFR_MA60, SOFR_Dev) 計算完成。")
        else:
            df['SOFR_MA60'] = np.nan
            df['SOFR_Dev'] = np.nan
            print(f"    - 警告：SOFR 數據點 ({len(df['SOFR'].dropna())}) 不足 {min_periods_ma}，無法計算均線和偏差。")
    else:
        df['SOFR_MA60'] = np.nan
        df['SOFR_Dev'] = np.nan
        print("    - 警告：缺少 SOFR 數據，無法計算均線和偏差。")

    # 計算持有量/準備金比率
    if 'Total_Gross_Positions_Millions' in df.columns and df['Total_Gross_Positions_Millions'].notna().any() and \
       'Reserves' in df.columns and df['Reserves'].notna().any():
        reserves_safe = df['Reserves'].replace(0, np.nan) # 避免除以零
        positions_numeric = pd.to_numeric(df['Total_Gross_Positions_Millions'], errors='coerce')
        df['Pos_Res_Ratio'] = positions_numeric / reserves_safe
        if np.isinf(df['Pos_Res_Ratio']).any():
             df['Pos_Res_Ratio'].replace([np.inf, -np.inf], np.nan, inplace=True)
        if df['Pos_Res_Ratio'].notna().any():
            print("    - 持有量/準備金比率 (Pos_Res_Ratio) 計算完成。")
        else:
            print("    - 警告：持有量/準備金比率計算後無有效值。")
    else:
        df['Pos_Res_Ratio'] = np.nan
        print("    - 警告：缺少持有量或準備金數據，無法計算持有量/準備金比率。")

    # 添加 SRF 列 (目前通常為 0，預留)
    if 'SRF_Amount_Billions' not in df.columns:
        df['SRF_Amount_Billions'] = 0.0
        print("    - 已添加 SRF_Amount_Billions 列並設為 0.0。")

    return df

def calculate_stress_index(df_with_indicators: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    計算交易商壓力指數。

    Args:
        df_with_indicators (pd.DataFrame): 包含基礎數據和已計算衍生指標的 DataFrame。
                                           預期欄位包括 'SOFR_Dev', 'Spread_10Y2Y',
                                           'Total_Gross_Positions_Millions', 'Volatility_Index',
                                           'VIX', 'Pos_Res_Ratio'。
        config (dict): 包含計算參數的字典，例如：
                       'rolling_window_days', 'weights', 'smoothing_window_stress_index',
                       'threshold_ratio_color' (用於 Pos/Res Ratio 的條件權重)。

    Returns:
        pd.DataFrame: 包含新增壓力指數相關欄位的 DataFrame。
                      新增欄位可能包括各成分排名、'Dealer_Stress_Index_Raw',
                      'Dealer_Stress_Index'。
    """
    print("正在計算壓力指數...")
    df = df_with_indicators.copy()

    window = int(config.get('rolling_window_days', 252))
    min_periods_rank = int(window * 0.6)
    weights = config.get('weights', {})
    threshold_ratio_val = config.get('threshold_ratio_color', 90) # 用於 Pos/Res Ratio 條件權重
    smoothing_window = int(config.get('smoothing_window_stress_index', 5))

    print(f"    - 使用滾動窗口: {window} 天, 最小期數: {min_periods_rank} 天")

    perc_ranks = pd.DataFrame(index=df.index)
    component_mapping = {
        'sofr_dev': 'SOFR_Dev',
        'spread_inv': 'Spread_10Y2Y', # 利差反轉
        'gross_pos': 'Total_Gross_Positions_Millions',
        'move': 'Volatility_Index', # 假設 merged_df 中已有此列 (來自 Yahoo)
        'vix': 'VIX', # 假設 merged_df 中已有此列 (來自 FRED)
        'pos_res_ratio': 'Pos_Res_Ratio'
    }

    available_components_for_ranking = {}
    for component_key, df_col_name in component_mapping.items():
        if df_col_name in df.columns and df[df_col_name].notna().any():
            if df[df_col_name].notna().sum() >= min_periods_rank:
                series_to_rank = df[df_col_name]
                rank_pct = series_to_rank.rolling(window=window, min_periods=min_periods_rank).rank(pct=True)
                if component_key == 'spread_inv': # 利差反轉
                    perc_ranks[component_key] = 1.0 - rank_pct
                else:
                    perc_ranks[component_key] = rank_pct
                available_components_for_ranking[component_key] = True
                print(f"        - 成分 '{component_key}' ({df_col_name}) 排名計算完成。")
            else:
                print(f"        - 警告：成分 '{component_key}' ({df_col_name}) 數據點不足 ({df[df_col_name].notna().sum()}/{min_periods_rank})，無法計算排名。")
                perc_ranks[component_key] = np.nan
                available_components_for_ranking[component_key] = False
        else:
            print(f"        - 警告：壓力指數計算缺少必要欄位 '{df_col_name}' (對應成分 '{component_key}')。")
            perc_ranks[component_key] = np.nan
            available_components_for_ranking[component_key] = False

    active_weights = {k: v for k, v in weights.items() if available_components_for_ranking.get(k, False) and v > 0}
    total_active_weight = sum(active_weights.values())

    if total_active_weight > 0:
        normalized_weights = {k: v / total_active_weight for k, v in active_weights.items()}
        print(f"        - 使用的指標與正規化權重: {', '.join([f'{k}({w:.1%})' for k, w in normalized_weights.items()])}")

        combined_score = pd.Series(0.0, index=df.index)
        for component_key, weight in normalized_weights.items():
            if component_key in perc_ranks.columns and perc_ranks[component_key].notna().any():
                rank_series_filled = perc_ranks[component_key].fillna(0.5) # 用 0.5 填充排名中的 NaN

                if component_key == 'pos_res_ratio':
                    # 條件權重：僅當 Pos_Res_Ratio >= threshold_ratio_val 時，此成分才貢獻權重
                    condition = (df['Pos_Res_Ratio'] >= threshold_ratio_val).astype(float).fillna(0.0)
                    combined_score += rank_series_filled * condition * weight
                    print(f"          * '{component_key}' 使用條件權重 (閾值 {threshold_ratio_val})。")
                else:
                    combined_score += rank_series_filled * weight
            else:
                 print(f"        - 警告：成分 '{component_key}' 的排名數據無效，未計入壓力指數。")


        df['Dealer_Stress_Index_Raw'] = (combined_score * 100).clip(0, 100)
        print(f"        - 原始壓力指數 (0-100) 計算完成 ({df['Dealer_Stress_Index_Raw'].notna().sum()} 點)。")

        if smoothing_window > 1:
            min_periods_smooth = max(1, int(smoothing_window * 0.5))
            df['Dealer_Stress_Index'] = df['Dealer_Stress_Index_Raw'].rolling(
                window=smoothing_window, min_periods=min_periods_smooth, center=True
            ).mean().clip(0, 100)
            print(f"        - 已執行指數平滑 (窗口: {smoothing_window} 天, 中心)。")
        else:
            df['Dealer_Stress_Index'] = df['Dealer_Stress_Index_Raw']
            print(f"        - 未執行指數平滑 (窗口 <= 1)。")
    else:
        print("        - 警告：無可用指標、權重為零或排名計算失敗，無法計算壓力指數。")
        df['Dealer_Stress_Index_Raw'] = np.nan
        df['Dealer_Stress_Index'] = np.nan

    # 將排名結果也合併回主 df，方便後續分析或調試
    for col_rank in perc_ranks.columns:
        df[f'Rank_{col_rank}'] = perc_ranks[col_rank]

    return df

def calculate_macd_momentum(df_with_stress_index: pd.DataFrame, config: dict) -> pd.DataFrame:
    """
    (可選) 計算壓力指數的 MACD 動能指標。

    Args:
        df_with_stress_index (pd.DataFrame): 包含 'Dealer_Stress_Index' 的 DataFrame。
        config (dict): 包含 MACD 計算參數的字典，例如：
                       'enable_macd_momentum_plot', 'macd_params': {'fast', 'slow', 'signal'},
                       'macd_colors': {'blue', 'green', 'red'}。

    Returns:
        pd.DataFrame: 包含新增 MACD 相關欄位的 DataFrame。
                      新增欄位可能包括 'Stress_Index_MACD_Hist', 'Stress_Index_MACD_Color'。
    """
    print("正在計算 MACD 動能指標 (如果啟用)...")
    df = df_with_stress_index.copy()

    enable_macd = config.get('enable_macd_momentum_plot', False)
    df['Stress_Index_MACD_Hist'] = np.nan # 初始化列
    df['Stress_Index_MACD_Color'] = 'grey' # 初始化顏色列

    if not enable_macd:
        print("    - MACD 未啟用，跳過計算。")
        return df

    if 'Dealer_Stress_Index' not in df.columns or not df['Dealer_Stress_Index'].notna().any():
        print("    - 警告：MACD 已啟用，但缺少有效的 'Dealer_Stress_Index' 數據，無法計算 MACD。")
        return df

    macd_params = config.get('macd_params', {})
    macd_colors = config.get('macd_colors', {})

    macd_fast = int(macd_params.get('fast', 12))
    macd_slow = int(macd_params.get('slow', 26))
    macd_signal = int(macd_params.get('signal', 9))
    color_blue = macd_colors.get('blue', "#6495ED")
    color_green = macd_colors.get('green', "#3CB371")
    color_red = macd_colors.get('red', "#B22222")

    print(f"    - 使用 MACD 參數: ({macd_fast}, {macd_slow}, {macd_signal})")
    base_series = df['Dealer_Stress_Index'].dropna()

    if len(base_series) > macd_slow: # 確保有足夠數據點
        ema_fast = base_series.ewm(span=macd_fast, adjust=False).mean()
        ema_slow = base_series.ewm(span=macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=macd_signal, adjust=False).mean()
        histogram = macd_line - signal_line
        df['Stress_Index_MACD_Hist'] = histogram.reindex(df.index) # 確保索引對齊原 df

        if df['Stress_Index_MACD_Hist'].notna().any():
            print(f"        - MACD Histogram 計算完成 ({df['Stress_Index_MACD_Hist'].count()} 點)")
            hist_series = df['Stress_Index_MACD_Hist']
            hist_diff = hist_series.diff()
            conditions = [
                (hist_diff > 0) & (hist_series >= 0),
                (hist_diff > 0) & (hist_series < 0),
                (hist_diff <= 0)
            ]
            colors = [color_blue, color_green, color_red]
            df['Stress_Index_MACD_Color'] = np.select(conditions, colors, default='grey')

            first_valid_index = hist_series.first_valid_index()
            if first_valid_index is not None:
                first_val = hist_series[first_valid_index]
                df.loc[first_valid_index, 'Stress_Index_MACD_Color'] = color_blue if first_val >=0 else color_green
            print("        - MACD 顏色計算完成。")
        else:
            print("        - 警告：MACD Histogram 計算結果均為 NaN。")
    else:
        print(f"    - 警告：壓力指數數據點 ({len(base_series)}) 不足以計算 MACD (需要至少 {macd_slow+1} 點)。")

    return df

# 範例使用 (僅為示意，實際由 run.py 調用)
if __name__ == '__main__':
    pass # 正式執行時此區塊不執行任何操作
