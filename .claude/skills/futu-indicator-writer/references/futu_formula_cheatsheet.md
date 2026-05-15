# 富途指标语法速查（实战简版）

> 说明：不同客户端版本在个别绘图函数参数上可能有差异。以下模板优先保证“逻辑可读 + 可改写 + 可快速排错”。

## 1. 常用数据字段

- `CLOSE` / `C`：收盘价
- `OPEN` / `O`：开盘价
- `HIGH` / `H`：最高价
- `LOW` / `L`：最低价
- `VOL`：成交量

## 2. 常用计算函数

- `MA(X, N)`：简单均线
- `EMA(X, N)`：指数均线
- `HHV(X, N)`：N 周期最高值
- `LLV(X, N)`：N 周期最低值
- `REF(X, N)`：引用 N 周期前
- `CROSS(A, B)`：A 上穿 B
- `ABS(X)`：绝对值

## 3. 逻辑写法

- 与：`AND`
- 或：`OR`
- 非：`NOT`

建议把复杂条件拆成中间变量：

```txt
TREND_OK := CLOSE > MA(CLOSE, 20);
VOL_OK := VOL > MA(VOL, 5);
BUY_SIG := CROSS(CLOSE, HHV(HIGH, 20)) AND TREND_OK AND VOL_OK;
```

## 3.1 两种常见语句风格（都要支持）

### 风格 A（中间变量风格，常见于信号脚本）

```txt
E9:=EMA(CLOSE,9);
E21:=EMA(CLOSE,21);
SIG:=CROSS(CLOSE,E9) AND E9>E21;
```

### 风格 B（输出线风格，常见于主图/副图线条）

```txt
A:EMA(HIGH,24),COLORBLUE;
B:EMA(LOW,23),COLORBLUE;
```

如果用户提供样例，优先跟随样例风格。允许 `:` 与 `:=` 混用（如 ZBBL），但要保持可读性和一致命名。

## 4. 绘图与信号标注（常用）

优先使用已验证样例同款写法（兼容性更稳）：

```txt
DRAWTEXT(BUY_SIG,LOW*0.98,'▲'),COLORGREEN;
DRAWTEXT(SELL_SIG,HIGH*1.02,'▼'),COLORRED;
```

说明：
- 颜色通常写在 `DRAWTEXT(...)` 之后，用 `,COLORXXX;` 结尾。
- 如字符显示异常，可改 `'B'/'S'` 或 `'买'/'卖'`。

`STICKLINE` 在主图带状/通道类指标中也很常见（如 NX 样式）：

```txt
STICKLINE(C>A,A,B,0.1,1),COLORBLUE;
STICKLINE(C<B,A,B,0.1,1),COLORBLUE;
```

### 4.1 副图信号线 + 图标

```txt
FAST := EMA(CLOSE, 12);
SLOW := EMA(CLOSE, 26);
DIF := FAST - SLOW;
DEA := EMA(DIF, 9);
HIST := 2 * (DIF - DEA);

BUY_SIG := CROSS(DIF, DEA) AND DIF < 0;
SELL_SIG := CROSS(DEA, DIF) AND DIF > 0;

DIF;
DEA;
HIST;

DRAWTEXT(BUY_SIG, DIF, '▲'),COLORGREEN;
DRAWTEXT(SELL_SIG, DIF, '▼'),COLORRED;
```

### 4.2 主图均线 + 买卖箭头

```txt
MA5 := MA(CLOSE, 5);
MA10 := MA(CLOSE, 10);
MA20 := MA(CLOSE, 20);

BUY_SIG := CROSS(MA5, MA10) AND CLOSE > MA20;
SELL_SIG := CROSS(MA10, MA5);

MA5;
MA10;
MA20;

DRAWTEXT(BUY_SIG, LOW*0.98, '▲'),COLORGREEN;
DRAWTEXT(SELL_SIG, HIGH*1.02, '▼'),COLORRED;
```

## 5. 三个高频模板

### 5.1 放量突破

```txt
N := 20;
M := 5;

BREAKOUT := CLOSE > HHV(HIGH, N-1);
VOL_OK := VOL > MA(VOL, M);
BUY_SIG := BREAKOUT AND VOL_OK;

DRAWTEXT(BUY_SIG, LOW*0.98, '▲'),COLORGREEN;
```

### 5.2 回踩均线企稳

```txt
MA20 := MA(CLOSE, 20);
PULLBACK := LOW <= MA20 * 1.01;
RECOVER := CLOSE > MA20;
BUY_SIG := PULLBACK AND RECOVER;

DRAWTEXT(BUY_SIG, LOW*0.98, '▲'),COLORGREEN;
```

### 5.3 双均线死叉止盈

```txt
MA5 := MA(CLOSE, 5);
MA10 := MA(CLOSE, 10);
SELL_SIG := CROSS(MA10, MA5);
DRAWTEXT(SELL_SIG, HIGH*1.02, '▼'),COLORRED;
```

## 6. 排错建议

1. 无信号：先放宽过滤条件（例如去掉量能过滤）
2. 信号过多：增加趋势过滤（`CLOSE > MA(CLOSE, 20)`）
3. 图标错位：买点用 `LOW`，卖点用 `HIGH`
4. 疑似未来函数：检查是否错误引用了未来柱数据
5. 样式报错：优先检查是否偏离了已验证样例（例如样例使用 `&&/||` 却被改成 `AND/OR`，或混合脚本被强行改写）
