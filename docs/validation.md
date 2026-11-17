# Statistical validation

A backtest says how the rules did on the prices they were built on. The checks here ask a
different question: how much of that result could be chance, or the effect of choosing with
hindsight? Each check reports a position or a range, never a verdict, and none of them says
anything about future results.

Results are labelled by what kind of evidence they are:

| Label | Meaning |
|---|---|
| **Past results** | How the rules did on past prices, which you could see while building them |
| **Robustness checks** | Tests on the same past prices that measure how much of the result could come from chance or from picking with hindsight |
| **Forward test** | Prices that arrived after the test started, so the rules could not have been built around them |

## What every backtest already includes

A plain backtest, with no extra tool run, already breaks its own result down several ways:

- **By calendar year.** The balance change, worst drop, and trades closed within each calendar
  year (UTC) of the period, so one good or bad year isn't hidden inside the total.
- **By market conditions.** Trades are split by whether the market was rising or falling (the
  previous day's close above or below its 50-day average) and by whether it was calm or busy
  (daily moves over the previous 20 days larger or smaller than the period's own middle value),
  so a result that depends on one kind of market is visible.
- **A 95% range for the share of trades won** (a Wilson interval), since a win rate from a
  handful of trades can look precise when it isn't.
- **95% ranges for the yearly Sharpe and Sortino ratios that don't treat trades as
  independent.** The headline Sharpe and Sortino ratios are worked out as usual; alongside them,
  a block bootstrap resamples runs of candle-to-candle balance changes (each run at least as
  long as the average trade, so one trade's candles, and trades that overlap, stay linked
  together) to give a second, wider range. When trades overlapped during the test, that count
  is shown too, as a reminder that their returns aren't independent of each other.

None of this changes the backtest's own headline numbers; it only shows how they break down.

## Train/test split

The *Backtest* tab can keep back the last 40%, 30% or 20% of the period (the API accepts
50–90% in-sample). The first part is tested as usual; the last part is traded again from a
fresh balance, with the same settings. The two results are shown side by side with the gap in
return. Rules tuned on the first part that do much worse on the last part may be fitted to the
past; the split cannot tell you which, only how big the gap is.

## Luck check

The *Luck check* tab runs several tests on the same backtest:

- **Enough trades?** The sample warning says when there are too few trades for the figures to
  mean much.
- **Block bootstrap.** Runs of consecutive trades are resampled thousands of times, keeping
  streaks together, to give a range for the return and the drawdown. The report says where the
  actual result sits in that range.
- **Both halves.** The period is split in two and each half is reported on its own, so a result
  that comes from one stretch is visible.
- **Random price paths.** The rules are run on up to 200 made-up price series built from the
  market's own candles (`trials` in the API). The share of random paths the real result beats is
  given with a 95% range (Wilson interval). Rules that do as well on made-up prices are not
  reading anything in the real ones.
- **Deflated Sharpe ratio.** *Settings tried so far* is how many versions or settings you have
  backtested, counting this one (blank means 1). The more you try, the more likely the best of
  them looks good by chance, so the bar the Sharpe ratio has to clear rises with that number.
- **Market states.** The trades are split by rising and falling, and by calm and busy, stretches
  of the market, so you can see whether the result depends on one kind of market.

## Optimiser checks

The *Optimiser* runs every combination of up to three settings and ranks them by return,
return ÷ worst drop, profit factor or Sharpe, with a minimum number of trades. Every run adds:

- **Keep back** (0–50% of the period): the ranking uses the first part only; the winner is then
  reported on the kept-back part, which played no part in choosing it. The combination that
  would have won on the kept-back part is named too, so you can see what was only knowable
  afterwards.
- **Walk-forward** (3–6 stretches, anchored): before each stretch the best combination so far is
  re-picked and traded on the next stretch; the chained result is compared with the hindsight
  best. With a kept-back period, walk-forward runs on the ranked part so the kept-back stretch
  stays unseen.
- **Probability of backtest overfitting** (8 stretches, 70 splits): how often the combination
  that ranks best in one half of the stretches ranks below the middle in the other half.
- The top combination's **deflated Sharpe ratio**, counting every combination tried.
- **Allowing for the number of combinations tried.** Each combination gets a one-sided
  statistical test of whether its average trade return was above zero, using the trades in the
  ranked part of the period. From the share that clear the usual 5% level on their own, two
  corrections are shown: the Bonferroni correction (strict: multiplies every combination's
  result by how many were tried) and the Benjamini-Hochberg correction (1995, which limits the
  expected share of chance passes among the ones that clear it). The chosen combination's own
  adjusted figures are shown alongside. This test treats trades as independent, so it's an
  approximation.
- **Neighbouring settings.** How the combinations one step away from the chosen one in the grid
  (on a number setting, with any option settings held the same) scored, against the chosen
  one. Called a *plateau* when their middle score is at least 75% of the chosen one's (the
  scores are fairly level around it), a *peak* when it's lower (the chosen setting stands above
  its neighbours). Neither says how a setting will do on new data.

*Apply settings* fills the form with a combination; it does not save, so you can run the luck
check on it first.

## Live versus backtest

Once a forward test has trades, its page compares them with the backtest, trade for trade
rather than by calendar time: the last N live trades (up to 30) are set against a block
bootstrap of N consecutive backtest trades, and the 10th–90th percentile of that is "the usual
range". For return, win rate, trades per month and drawdown the live figure is reported as
**below, within or above** the usual range. Costs are shown as gross P&L against spread and
funding, so a shortfall can be traced to costs rather than to the rules. These are positions
only; there is no score.

## Robustness summary

The *Testing* screen keeps one **Robustness summary** that gathers the latest backtest, luck
check and optimiser results on the page into one place: the by-year and by-market-condition
breakdowns, the sample-size ranges, the worst-drop spread from reshuffled trades, the
train/test split or optimiser kept-back and walk-forward comparisons, and the optimiser's
parameter sensitivity (multiple testing and neighbouring settings). Only the parts whose tool
has actually been run are shown, and it updates in place as you run more of them. Like
everything else on this page, it's gathered facts about past prices, not a score.

## Comparing versions

*History → Compare results* backtests two to four saved versions of a strategy over the same
period, side by side, with a *Change* column when there are two. It shows what an edit changed;
it does not rank the versions.

## Reading the results

- Every figure is about these rules on these prices. A result inside a bootstrap range, or a
  good share of random paths beaten, is a fact about that test and not a prediction.
- The checks are more useful with more trades; the sample warning applies to all of them.
- The more settings and versions you try, the more the deflated Sharpe ratio and the overfitting
  probability matter, because the best of many tries is partly luck by construction.
- A forward test is the only check that uses prices the rules could not have been built on.
