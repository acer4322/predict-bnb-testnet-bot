use std::{
    cell::RefCell,
    cmp::Ordering,
    collections::{HashMap, HashSet},
    rc::Rc,
};

use crate::{
    backtest::{
        BacktestError,
        assettype::AssetType,
        models::{FeeModel, LatencyModel, QueueModel},
        order::ExchToLocal,
        proc::Processor,
        state::State,
    },
    depth::{INVALID_MAX, INVALID_MIN, L2MarketDepth, MarketDepth},
    prelude::OrdType,
    types::{
        EXCH_ASK_DEPTH_CLEAR_EVENT,
        EXCH_ASK_DEPTH_EVENT,
        EXCH_ASK_DEPTH_SNAPSHOT_EVENT,
        EXCH_BID_DEPTH_CLEAR_EVENT,
        EXCH_BID_DEPTH_EVENT,
        EXCH_BID_DEPTH_SNAPSHOT_EVENT,
        EXCH_BUY_TRADE_EVENT,
        EXCH_DEPTH_CLEAR_EVENT,
        EXCH_EVENT,
        EXCH_SELL_TRADE_EVENT,
        Event,
        Order,
        OrderId,
        Side,
        Status,
        TimeInForce,
    },
};

/// The exchange model with partial fills.
///
/// * Support order types: [OrdType::Limit](crate::types::OrdType::Limit)
/// * Support time-in-force: [`TimeInForce::GTC`], [`TimeInForce::FOK`], [`TimeInForce::IOC`],
///   [`TimeInForce::GTX`]
///
/// **Conditions for Full Execution**
/// Buy order in the order book
///
/// - Your order price >= the best ask price
/// - Your order price > sell trade price
///
/// Sell order in the order book
///
/// - Your order price <= the best bid price
/// - Your order price < buy trade price
///
/// **Conditions for Partial Execution**
/// Buy order in the order book
///
/// - Filled by (remaining) sell trade quantity: your order is at the front of the queue && your
///   order price == sell trade price
///
/// Sell order in the order book
///
/// - Filled by (remaining) buy trade quantity: your order is at the front of the queue && your
///   order price == buy trade price
///
/// **Liquidity-Taking Order**
/// Liquidity-taking orders will be executed based on the quantity of the order book, even though
/// the best price and quantity do not change due to your execution. Be aware that this may cause
/// unrealistic fill simulations if you attempt to execute a large quantity.
///
/// **General Comment**
/// Simulating partial fills accurately can be challenging, as they may indicate potential market
/// impact. The rule of thumb is to ensure that your backtesting results align with your live
/// results.
/// (more comment will be added...)
///
pub struct PartialFillExchange<AT, LM, QM, MD, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    QM: QueueModel<MD>,
    MD: MarketDepth,
    FM: FeeModel,
{
    // key: order_id, value: Order
    orders: Rc<RefCell<HashMap<OrderId, Order>>>,
    // key: order's price tick, value: order_ids
    buy_orders: HashMap<i64, HashSet<OrderId>>,
    sell_orders: HashMap<i64, HashSet<OrderId>>,

    order_e2l: ExchToLocal<LM>,

    depth: MD,
    state: State<AT, FM>,
    queue_model: QM,

    filled_orders: Vec<OrderId>,
    // Request-time executions are kept individually until the final ACK is known.
    pending_request_fills: Vec<Order>,
    consumed_bids: HashMap<i64, f64>,
    consumed_asks: HashMap<i64, f64>,
    priority: HashMap<OrderId, u64>,
    next_priority: u64,
}

impl<AT, LM, QM, MD, FM> PartialFillExchange<AT, LM, QM, MD, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    QM: QueueModel<MD>,
    MD: MarketDepth,
    FM: FeeModel,
{
    /// Constructs an instance of `PartialFillExchange`.
    pub fn new(
        depth: MD,
        state: State<AT, FM>,
        queue_model: QM,
        order_e2l: ExchToLocal<LM>,
    ) -> Self {
        Self {
            orders: Default::default(),
            buy_orders: Default::default(),
            sell_orders: Default::default(),
            order_e2l,
            depth,
            state,
            queue_model,
            filled_orders: Default::default(),
            pending_request_fills: Default::default(),
            consumed_bids: Default::default(),
            consumed_asks: Default::default(),
            priority: Default::default(),
            next_priority: 0,
        }
    }

    // Numerical closure only: never lot-size rounding or a policy cutoff.
    fn capped_exec(leaves: f64, available: f64) -> f64 {
        let roundoff = 8.0 * f64::EPSILON * leaves.abs().max(available.abs());
        if available > 0.0 && leaves - available <= roundoff {
            leaves
        } else {
            leaves.min(available)
        }
    }

    fn check_if_sell_filled(
        &mut self,
        order: &mut Order,
        price_tick: i64,
        qty: f64,
        timestamp: i64,
    ) -> Result<(), BacktestError> {
        match order.price_tick.cmp(&price_tick) {
            Ordering::Greater => {}
            Ordering::Less => {
                let exec_qty = Self::capped_exec(order.leaves_qty, qty);
                if exec_qty >= order.leaves_qty { self.filled_orders.push(order.order_id); }
                return self.fill::<true>(
                    order,
                    timestamp,
                    true,
                    order.price_tick,
                    exec_qty,
                );
            }
            Ordering::Equal => {
                // Updates the order's queue position.
                self.queue_model.trade(order, qty, &self.depth);
                let filled_qty = Self::capped_exec(order.leaves_qty, self.queue_model.is_filled(order, &self.depth).min(qty));
                if filled_qty > 0.0 {
                    // q_ahead is negative since is_filled is true and its value represents the
                    // executable quantity of this order after execution in the queue ahead of this
                    // order.
                    let exec_qty = if filled_qty >= order.leaves_qty {
                        self.filled_orders.push(order.order_id);
                        order.leaves_qty
                    } else {
                        filled_qty
                    };
                    return self.fill::<true>(order, timestamp, true, order.price_tick, exec_qty);
                }
            }
        }
        Ok(())
    }

    fn check_if_buy_filled(
        &mut self,
        order: &mut Order,
        price_tick: i64,
        qty: f64,
        timestamp: i64,
    ) -> Result<(), BacktestError> {
        match order.price_tick.cmp(&price_tick) {
            Ordering::Greater => {
                let exec_qty = Self::capped_exec(order.leaves_qty, qty);
                if exec_qty >= order.leaves_qty { self.filled_orders.push(order.order_id); }
                return self.fill::<true>(
                    order,
                    timestamp,
                    true,
                    order.price_tick,
                    exec_qty,
                );
            }
            Ordering::Less => {}
            Ordering::Equal => {
                // Updates the order's queue position.
                self.queue_model.trade(order, qty, &self.depth);
                let filled_qty = Self::capped_exec(order.leaves_qty, self.queue_model.is_filled(order, &self.depth).min(qty));
                if filled_qty > 0.0 {
                    // q_ahead is negative since is_filled is true and its value represents the
                    // executable quantity of this order after execution in the queue ahead of this
                    // order.
                    let exec_qty = if filled_qty >= order.leaves_qty {
                        self.filled_orders.push(order.order_id);
                        order.leaves_qty
                    } else {
                        filled_qty
                    };
                    return self.fill::<true>(order, timestamp, true, order.price_tick, exec_qty);
                }
            }
        }
        Ok(())
    }

    fn fill<const MAKE_RESPONSE: bool>(
        &mut self,
        order: &mut Order,
        timestamp: i64,
        maker: bool,
        exec_price_tick: i64,
        exec_qty: f64,
    ) -> Result<(), BacktestError> {
        if order.status == Status::Expired
            || order.status == Status::Canceled
            || order.status == Status::Filled
        {
            return Err(BacktestError::InvalidOrderStatus);
        }

        assert!(exec_qty > 0.0 && exec_qty <= order.leaves_qty);
        if !maker {
            self.consume_depth(order.side == Side::Buy, exec_price_tick, exec_qty);
        }
        order.maker = maker;
        if maker {
            order.exec_price_tick = order.price_tick;
        } else {
            order.exec_price_tick = exec_price_tick;
        }

        order.exec_qty = exec_qty;
        order.leaves_qty -= exec_qty;
        if order.leaves_qty > 0.0 {
            order.status = Status::PartiallyFilled;
        } else {
            order.status = Status::Filled;
        }
        order.exch_timestamp = timestamp;

        self.state.apply_fill(order);

        if MAKE_RESPONSE {
            self.order_e2l.respond(order.clone());
        } else {
            self.pending_request_fills.push(order.clone());
        }
        Ok(())
    }

    fn remove_filled_orders(&mut self) {
        if !self.filled_orders.is_empty() {
            let mut orders = self.orders.borrow_mut();
            for order_id in self.filled_orders.drain(..) {
                let order = orders.remove(&order_id).unwrap();
                if order.side == Side::Buy {
                    self.buy_orders
                        .get_mut(&order.price_tick)
                        .unwrap()
                        .remove(&order_id);
                } else {
                    self.sell_orders
                        .get_mut(&order.price_tick)
                        .unwrap()
                        .remove(&order_id);
                }
            }
        }
    }

    fn on_bid_qty_chg(&mut self, price_tick: i64, prev_qty: f64, new_qty: f64) {
        let orders = self.orders.clone();
        if let Some(order_ids) = self.buy_orders.get(&price_tick) {
            for order_id in order_ids.iter() {
                let mut orders_borrowed = orders.borrow_mut();
                let order = orders_borrowed.get_mut(order_id).unwrap();
                self.queue_model
                    .depth(order, prev_qty, new_qty, &self.depth);
            }
        }
    }

    fn on_ask_qty_chg(&mut self, price_tick: i64, prev_qty: f64, new_qty: f64) {
        let orders = self.orders.clone();
        if let Some(order_ids) = self.sell_orders.get(&price_tick) {
            for order_id in order_ids.iter() {
                let mut orders_borrowed = orders.borrow_mut();
                let order = orders_borrowed.get_mut(order_id).unwrap();
                self.queue_model
                    .depth(order, prev_qty, new_qty, &self.depth);
            }
        }
    }


    // Explicit counterfactual depth accounting; shared across all OUR orders.
    fn available_depth(&self, asks: bool, price: i64) -> f64 {
        let (raw, used) = if asks {
            (self.depth.ask_qty_at_tick(price), self.consumed_asks.get(&price))
        } else {
            (self.depth.bid_qty_at_tick(price), self.consumed_bids.get(&price))
        };
        (raw - used.copied().unwrap_or(0.0)).max(0.0)
    }

    fn consume_depth(&mut self, asks: bool, price: i64, qty: f64) {
        assert!(qty >= 0.0 && qty <= self.available_depth(asks, price) + 1e-10);
        let used = if asks { &mut self.consumed_asks } else { &mut self.consumed_bids };
        *used.entry(price).or_default() += qty;
    }

    fn retire_depth(&mut self, asks: bool, price: i64, before: f64, after: f64) {
        let used = if asks { &mut self.consumed_asks } else { &mut self.consumed_bids };
        if let Some(debt) = used.get_mut(&price) {
            *debt = (*debt - (before - after).max(0.0)).max(0.0);
        }
    }

    fn register_resting(&mut self, order: &mut Order, timestamp: i64) {
        self.queue_model.new_order(order, &self.depth);
        order.status = if order.leaves_qty < order.qty { Status::PartiallyFilled } else { Status::New };
        order.exch_timestamp = timestamp;
        self.next_priority += 1;
        self.priority.insert(order.order_id, self.next_priority);
        let levels = if order.side == Side::Buy { &mut self.buy_orders } else { &mut self.sell_orders };
        levels.entry(order.price_tick).or_default().insert(order.order_id);
        self.orders.borrow_mut().insert(order.order_id, order.clone());
    }

    fn ordered_ids(&self, side: Side) -> Vec<OrderId> {
        let orders = self.orders.borrow();
        let mut ids: Vec<_> = orders.values().filter(|o| o.side == side).map(|o| o.order_id).collect();
        ids.sort_by(|a, b| {
            let oa = &orders[a]; let ob = &orders[b];
            let price = if side == Side::Buy { ob.price_tick.cmp(&oa.price_tick) } else { oa.price_tick.cmp(&ob.price_tick) };
            price.then(self.priority[a].cmp(&self.priority[b]))
        });
        ids
    }

    fn match_crossing_residuals(&mut self, timestamp: i64) -> Result<(), BacktestError> {
        for side in [Side::Buy, Side::Sell] {
            let ids = self.ordered_ids(side);
            let orders = self.orders.clone();
            let mut borrowed = orders.borrow_mut();
            for id in ids {
                let order = borrowed.get_mut(&id).unwrap();
                let asks = side == Side::Buy;
                let best = if asks { self.depth.best_ask_tick() } else { self.depth.best_bid_tick() };
                if (asks && (best == INVALID_MAX || best > order.price_tick))
                    || (!asks && (best == INVALID_MIN || best < order.price_tick)) { continue; }
                let prices: Vec<i64> = if asks { (best..=order.price_tick).collect() }
                    else { (order.price_tick..=best).rev().collect() };
                for p in prices {
                    let qty = self.available_depth(asks, p).min(order.leaves_qty);
                    if qty <= 0.0 { continue; }
                    self.consume_depth(asks, p, qty);
                    self.fill::<true>(order, timestamp, true, order.price_tick, qty)?;
                    if order.status == Status::Filled {
                        self.filled_orders.push(id);
                        break;
                    }
                }
            }
            drop(borrowed);
            self.remove_filled_orders();
        }
        Ok(())
    }

    fn shared_trade(&mut self, side: Side, price: i64, qty: f64, timestamp: i64) -> Result<(), BacktestError> {
        assert!(qty.is_finite() && qty >= 0.0);
        let ids = self.ordered_ids(side);
        let orders = self.orders.clone();
        let mut borrowed = orders.borrow_mut();
        let mut remaining = qty;
        for id in ids {
            if remaining <= 0.0 { break; }
            let order = borrowed.get_mut(&id).unwrap();
            let before = order.leaves_qty;
            // Prior OUR fills reduce the print offered to each later queue.
            if side == Side::Buy { self.check_if_buy_filled(order, price, remaining, timestamp)?; }
            else { self.check_if_sell_filled(order, price, remaining, timestamp)?; }
            let filled = before - order.leaves_qty;
            assert!(filled >= 0.0 && filled <= remaining + 1e-10);
            remaining = (remaining - filled).max(0.0);
        }
        drop(borrowed);
        self.remove_filled_orders();
        Ok(())
    }

    fn ack_new(&mut self, order: &mut Order, timestamp: i64) -> Result<(), BacktestError> {
        if self.orders.borrow().contains_key(&order.order_id) {
            return Err(BacktestError::OrderIdExist);
        }

        // Self-cross needs a venue STP contract; stop rather than invent execution.
        if self.orders.borrow().values().any(|o| o.side != order.side
            && ((order.side == Side::Buy && order.price_tick >= o.price_tick)
                || (order.side == Side::Sell && order.price_tick <= o.price_tick))) {
            return Err(BacktestError::InvalidOrderRequest);
        }
        if order.side == Side::Buy {
            match order.order_type {
                OrdType::Limit => {
                    // Checks if the buy order price is greater than or equal to the current best ask.
                    if order.price_tick >= self.depth.best_ask_tick() {
                        match order.time_in_force {
                            TimeInForce::GTX => {
                                order.status = Status::Expired;
                                order.exch_timestamp = timestamp;
                                Ok(())
                            }
                            TimeInForce::FOK => {
                                // The order must be executed immediately in its entirety; otherwise, the
                                // entire order will be cancelled.
                                let mut execute = false;
                                let mut cum_qty = 0f64;
                                for t in self.depth.best_ask_tick()..=order.price_tick {
                                    cum_qty += self.available_depth(true, t);
                                    if cum_qty >= order.qty
                                    {
                                        execute = true;
                                        break;
                                    }
                                }
                                if execute {
                                    for t in self.depth.best_ask_tick()..=order.price_tick {
                                        let qty = self.available_depth(true, t);
                                        if qty > 0.0 {
                                            let exec_qty = Self::capped_exec(order.leaves_qty, qty);
                                            self.fill::<false>(
                                                order, timestamp, false, t, exec_qty,
                                            )?;
                                            if order.status == Status::Filled {
                                                return Ok(());
                                            }
                                        }
                                    }
                                    unreachable!();
                                } else {
                                    order.status = Status::Expired;
                                    order.exch_timestamp = timestamp;
                                    Ok(())
                                }
                            }
                            TimeInForce::IOC => {
                                // The order must be executed immediately.
                                for t in self.depth.best_ask_tick()..=order.price_tick {
                                    let qty = self.available_depth(true, t);
                                    if qty > 0.0 {
                                        let exec_qty = Self::capped_exec(order.leaves_qty, qty);
                                        self.fill::<false>(order, timestamp, false, t, exec_qty)?;
                                    }
                                    if order.status == Status::Filled {
                                        return Ok(());
                                    }
                                }
                                order.status = Status::Expired;
                                order.exch_timestamp = timestamp;
                                Ok(())
                            }
                            TimeInForce::GTC => {
                                // Takes the market.
                                for t in self.depth.best_ask_tick()..=order.price_tick {
                                    let qty = self.available_depth(true, t);
                                    if qty > 0.0 {
                                        let exec_qty = Self::capped_exec(order.leaves_qty, qty);
                                        self.fill::<false>(order, timestamp, false, t, exec_qty)?;
                                    }
                                    if order.status == Status::Filled {
                                        return Ok(());
                                    }
                                }

                                self.register_resting(order, timestamp);
                                Ok(())
                            }
                            TimeInForce::Unsupported => Err(BacktestError::InvalidOrderRequest),
                        }
                    } else {
                        match order.time_in_force {
                            TimeInForce::GTC | TimeInForce::GTX => {
                                self.register_resting(order, timestamp);
                                Ok(())
                            }
                            TimeInForce::FOK | TimeInForce::IOC => {
                                order.status = Status::Expired;
                                order.exch_timestamp = timestamp;
                                Ok(())
                            }
                            TimeInForce::Unsupported => Err(BacktestError::InvalidOrderRequest),
                        }
                    }
                }
                OrdType::Market => {
                    // todo: set the proper upper bound.
                    for t in self.depth.best_ask_tick()..(self.depth.best_ask_tick() + 100) {
                        let qty = self.available_depth(true, t);
                        if qty > 0.0 {
                            let exec_qty = Self::capped_exec(order.leaves_qty, qty);
                            self.fill::<false>(order, timestamp, false, t, exec_qty)?;
                        }
                        if order.status == Status::Filled {
                            return Ok(());
                        }
                    }
                    order.status = Status::Expired;
                    order.exch_timestamp = timestamp;
                    Ok(())
                }
                OrdType::Unsupported => Err(BacktestError::InvalidOrderRequest),
            }
        } else {
            match order.order_type {
                OrdType::Limit => {
                    // Checks if the sell order price is less than or equal to the current best bid.
                    if order.price_tick <= self.depth.best_bid_tick() {
                        match order.time_in_force {
                            TimeInForce::GTX => {
                                order.status = Status::Expired;
                                order.exch_timestamp = timestamp;
                                Ok(())
                            }
                            TimeInForce::FOK => {
                                // The order must be executed immediately in its entirety; otherwise, the
                                // entire order will be cancelled.
                                let mut execute = false;
                                let mut cum_qty = 0f64;
                                for t in (order.price_tick..=self.depth.best_bid_tick()).rev() {
                                    cum_qty += self.available_depth(false, t);
                                    if cum_qty >= order.qty
                                    {
                                        execute = true;
                                        break;
                                    }
                                }
                                if execute {
                                    for t in (order.price_tick..=self.depth.best_bid_tick()).rev() {
                                        let qty = self.available_depth(false, t);
                                        if qty > 0.0 {
                                            let exec_qty = Self::capped_exec(order.leaves_qty, qty);
                                            self.fill::<false>(
                                                order, timestamp, false, t, exec_qty,
                                            )?;
                                            if order.status == Status::Filled {
                                                return Ok(());
                                            }
                                        }
                                    }
                                    unreachable!();
                                } else {
                                    order.status = Status::Expired;
                                    order.exch_timestamp = timestamp;
                                    Ok(())
                                }
                            }
                            TimeInForce::IOC => {
                                // The order must be executed immediately.
                                for t in (order.price_tick..=self.depth.best_bid_tick()).rev() {
                                    let qty = self.available_depth(false, t);
                                    if qty > 0.0 {
                                        let exec_qty = Self::capped_exec(order.leaves_qty, qty);
                                        self.fill::<false>(order, timestamp, false, t, exec_qty)?;
                                    }
                                    if order.status == Status::Filled {
                                        return Ok(());
                                    }
                                }
                                order.status = Status::Expired;
                                order.exch_timestamp = timestamp;
                                Ok(())
                            }
                            TimeInForce::GTC => {
                                // Takes the market.
                                for t in (order.price_tick..=self.depth.best_bid_tick()).rev() {
                                    let qty = self.available_depth(false, t);
                                    if qty > 0.0 {
                                        let exec_qty = Self::capped_exec(order.leaves_qty, qty);
                                        self.fill::<false>(order, timestamp, false, t, exec_qty)?;
                                    }
                                    if order.status == Status::Filled {
                                        return Ok(());
                                    }
                                }

                                self.register_resting(order, timestamp);
                                Ok(())
                            }
                            _ => {
                                unreachable!();
                            }
                        }
                    } else {
                        match order.time_in_force {
                            TimeInForce::GTC | TimeInForce::GTX => {
                                self.register_resting(order, timestamp);
                                Ok(())
                            }
                            TimeInForce::FOK | TimeInForce::IOC => {
                                order.status = Status::Expired;
                                order.exch_timestamp = timestamp;
                                Ok(())
                            }
                            TimeInForce::Unsupported => Err(BacktestError::InvalidOrderRequest),
                        }
                    }
                }
                OrdType::Market => {
                    // todo: set the proper lower bound.
                    for t in ((self.depth.best_bid_tick() - 100)..=self.depth.best_bid_tick()).rev()
                    {
                        let qty = self.available_depth(false, t);
                        if qty > 0.0 {
                            let exec_qty = Self::capped_exec(order.leaves_qty, qty);
                            self.fill::<false>(order, timestamp, false, t, exec_qty)?;
                        }
                        if order.status == Status::Filled {
                            return Ok(());
                        }
                    }
                    order.status = Status::Expired;
                    order.exch_timestamp = timestamp;
                    Ok(())
                }
                OrdType::Unsupported => Err(BacktestError::InvalidOrderRequest),
            }
        }
    }

    fn ack_cancel(&mut self, order: &mut Order, timestamp: i64) -> Result<(), BacktestError> {
        let exch_order = {
            let mut order_borrowed = self.orders.borrow_mut();
            order_borrowed.remove(&order.order_id)
        };

        if exch_order.is_none() {
            order.req = Status::Rejected;
            order.exch_timestamp = timestamp;
            return Ok(());
        }

        let exch_order = exch_order.unwrap();
        let _ = std::mem::replace(order, exch_order);

        // Deletes the order.
        if order.side == Side::Buy {
            self.buy_orders
                .get_mut(&order.price_tick)
                .unwrap()
                .remove(&order.order_id);
        } else {
            self.sell_orders
                .get_mut(&order.price_tick)
                .unwrap()
                .remove(&order.order_id);
        }
        order.status = Status::Canceled;
        order.exch_timestamp = timestamp;
        Ok(())
    }

    fn ack_modify<const RESET_QUEUE_POS: bool>(
        &mut self,
        order: &mut Order,
        timestamp: i64,
    ) -> Result<(), BacktestError> {
        let (prev_order_price_tick, prev_leaves_qty) = {
            let order_borrowed = self.orders.borrow();
            let exch_order = order_borrowed.get(&order.order_id);

            // The order can be already deleted due to fill or expiration.
            if exch_order.is_none() {
                order.req = Status::Rejected;
                order.exch_timestamp = timestamp;
                return Ok(());
            }

            let exch_order = exch_order.unwrap();
            (exch_order.price_tick, exch_order.leaves_qty)
        };

        // The initialization of the order queue position may not occur when the modified quantity
        // is smaller than the previous quantity, depending on the exchanges. It may need to
        // implement exchange-specific specialization.
        if RESET_QUEUE_POS
            || prev_order_price_tick != order.price_tick
            || order.qty > prev_leaves_qty
        {
            let mut cancel_order = order.clone();
            self.ack_cancel(&mut cancel_order, timestamp)?;
            self.ack_new(order, timestamp)?;
        } else {
            let mut order_borrowed = self.orders.borrow_mut();
            let exch_order = order_borrowed.get_mut(&order.order_id);
            let exch_order = exch_order.unwrap();

            exch_order.qty = order.qty;
            exch_order.leaves_qty = order.qty;
            exch_order.exch_timestamp = timestamp;
            order.leaves_qty = order.qty;
            order.exch_timestamp = timestamp;
        }
        Ok(())
    }
}

impl<AT, LM, QM, MD, FM> Processor for PartialFillExchange<AT, LM, QM, MD, FM>
where
    AT: AssetType,
    LM: LatencyModel,
    QM: QueueModel<MD>,
    MD: MarketDepth + L2MarketDepth,
    FM: FeeModel,
{
    fn event_seen_timestamp(&self, event: &Event) -> Option<i64> {
        event.is(EXCH_EVENT).then_some(event.exch_ts)
    }

    fn process(&mut self, event: &Event) -> Result<(), BacktestError> {
        if event.is(EXCH_BID_DEPTH_CLEAR_EVENT) {
            self.depth.clear_depth(Side::Buy, event.px);
            self.consumed_bids.retain(|p, _| self.depth.bid_qty_at_tick(*p) > 0.0);
        } else if event.is(EXCH_ASK_DEPTH_CLEAR_EVENT) {
            self.depth.clear_depth(Side::Sell, event.px);
            self.consumed_asks.retain(|p, _| self.depth.ask_qty_at_tick(*p) > 0.0);
        } else if event.is(EXCH_DEPTH_CLEAR_EVENT) {
            self.depth.clear_depth(Side::None, event.px);
            self.consumed_bids.retain(|p, _| self.depth.bid_qty_at_tick(*p) > 0.0);
            self.consumed_asks.retain(|p, _| self.depth.ask_qty_at_tick(*p) > 0.0);
        } else if event.is(EXCH_BID_DEPTH_EVENT) || event.is(EXCH_BID_DEPTH_SNAPSHOT_EVENT) {
            let (p, _, _, prev, new, ts) = self.depth.update_bid_depth(event.px, event.qty, event.exch_ts);
            self.retire_depth(false, p, prev, new);
            self.on_bid_qty_chg(p, prev, new);
            self.match_crossing_residuals(ts)?;
        } else if event.is(EXCH_ASK_DEPTH_EVENT) || event.is(EXCH_ASK_DEPTH_SNAPSHOT_EVENT) {
            let (p, _, _, prev, new, ts) = self.depth.update_ask_depth(event.px, event.qty, event.exch_ts);
            self.retire_depth(true, p, prev, new);
            self.on_ask_qty_chg(p, prev, new);
            self.match_crossing_residuals(ts)?;
        } else if event.is(EXCH_BUY_TRADE_EVENT) {
            let p = (event.px / self.depth.tick_size()).round() as i64;
            self.shared_trade(Side::Sell, p, event.qty, event.exch_ts)?;
        } else if event.is(EXCH_SELL_TRADE_EVENT) {
            let p = (event.px / self.depth.tick_size()).round() as i64;
            self.shared_trade(Side::Buy, p, event.qty, event.exch_ts)?;
        }
        Ok(())
    }

    fn process_recv_order(
        &mut self,
        timestamp: i64,
        _wait_resp_order_id: Option<OrderId>,
    ) -> Result<bool, BacktestError> {
        while let Some(mut order) = self.order_e2l.receive(timestamp) {
            assert!(self.pending_request_fills.is_empty());
            // Processes a new order.
            if order.req == Status::New {
                order.req = Status::None;
                self.ack_new(&mut order, timestamp)?;
            }
            // Processes a cancel order.
            else if order.req == Status::Canceled {
                order.req = Status::None;
                self.ack_cancel(&mut order, timestamp)?;
            }
            // Processes a modify order.
            else if order.req == Status::Replaced {
                order.req = Status::None;
                // Modification needs a separate residual/priority contract.
                return Err(BacktestError::InvalidOrderRequest);
            } else {
                return Err(BacktestError::InvalidOrderRequest);
            }
            // The final fill ACK already carries the last execution. Emit only
            // preceding increments separately, so neither quantity nor fees are
            // collapsed to the last price or counted twice. Expiry/cancel ACKs
            // carry no fill: all preceding executions must be emitted.
            let fill_ack = (order.status == Status::Filled
                || order.status == Status::PartiallyFilled)
                && order.req != Status::Rejected;
            if fill_ack {
                if let Some(last) = self.pending_request_fills.pop() {
                    assert_eq!(last.status, order.status);
                    assert_eq!(last.exec_qty, order.exec_qty);
                    assert_eq!(last.exec_price_tick, order.exec_price_tick);
                    assert_eq!(last.leaves_qty, order.leaves_qty);
                } else {
                    // A lifecycle-only acknowledgement is not a new execution.
                    order.exec_qty = 0.0;
                }
            }
            for fill in self.pending_request_fills.drain(..) {
                self.order_e2l.respond(fill);
            }
            self.order_e2l.respond(order);
        }
        Ok(false)
    }

    fn earliest_recv_order_timestamp(&self) -> i64 {
        self.order_e2l
            .earliest_recv_order_timestamp()
            .unwrap_or(i64::MAX)
    }

    fn earliest_send_order_timestamp(&self) -> i64 {
        self.order_e2l
            .earliest_send_order_timestamp()
            .unwrap_or(i64::MAX)
    }
}
