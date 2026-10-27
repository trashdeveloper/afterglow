// tb_darkriscv_sltiu: REAL discriminant test for the darkriscv
// unsigned-immediate mux (stranger #2, different design from SERV).
//
// Historical upstream fix (darklife/darkriscv@3dae843, "Bugfix: removing
// unsigned immediates", 2026-01-06, GabPGomes): U2REGX carried the
// ZERO-extended immediate (UIMM) instead of the SIGN-extended one
// (SIMM) for immediate-group ALU ops:
//
//   - wire [31:0] U2REGX = XMCC ? UIMM : U2REG;  (bug, pre-fix)
//   + wire [31:0] U2REGX = XMCC ? SIMM : U2REG;  (anchor, post-fix)
//
// The TB boots the real core with a 4-instruction program:
//
//   lui   x1, 1          // x1 = 0x1000
//   addi  x1, x1, 904    // x1 = 5000
//   sltiu x3, x1, -1     // x3 = (5000 < -1) ? 1 : 0
//   sw    x3, 0(x0)      // TB observes the store on the D-bus
//
// SLTIU compares against the sign-extended immediate (-1 = 0xFFFFFFFF),
// so the correct result is x3 = 1. Under the bug the compare uses the
// zero-extended immediate (0xFFF = 4095), so 5000 < 4095 is false and
// x3 = 0. The D-bus store discriminates: DATAO==1 is PASS, 0 is FAIL.
// Sanity: the lui/addi pair proves the harness drives the pipeline
// (a dead core stores nothing and the timeout fires RED instead).
// Prints "tohost: PASS" or "tohost: FAIL 0xdead" + "TRAP: ..." +
// "CYCLES=<n>". Deterministic. Accepts +seed=<n> (logged only).
module tb_darkriscv_sltiu;
	reg clk = 0;
	always #5 clk = ~clk;

	reg res = 1;

	// I-bus (Harvard ROM, zero wait states).
	wire        idreq;
	wire [31:0] iaddr;
	reg  [31:0] idata = 32'h00000013;
	wire        iberr;

	// D-bus (single store observed, zero wait states).
	wire        ddreq;
	wire [31:0] daddr;
	wire [ 2:0] dlen;
	wire [ 3:0] dbe;
	wire        drw;
	wire        drd;
	wire        dwr;
	wire [31:0] datao;
	reg  [31:0] datai = 32'b0;
	wire        dberr;
	wire [ 3:0] dbg;

	assign iberr = 1'b0;
	wire idack;
	assign idack = 1'b1;
	assign dberr = 1'b0;
	// Zero-wait-state slave: ack follows request combinationally.
	wire ddack;
	assign ddack = ddreq;

	darkriscv dut (
		.CLK(clk),
		.RES(res),
		.IRQ(1'b0),
		.IDREQ(idreq),
		.IADDR(iaddr),
		.IDATA(idata),
		.IDACK(idack),
		.IBERR(iberr),
		.DDREQ(ddreq),
		.DADDR(daddr),
		.DLEN(dlen),
		.DBE(dbe),
		.DRW(drw),
		.DRD(drd),
		.DWR(dwr),
		.DATAO(datao),
		.DATAI(datai),
		.DDACK(ddack),
		.DBERR(dberr),
		.DEBUG(dbg)
	);

	// Program ROM (word-addressed). Four leading NOPs absorb the
	// post-reset pipeline fill (FLUSH=2 drops the first fetches, so
	// a real instruction at pc=0 would never write back).
	reg [31:0] rom [0:63];
	integer k;
	initial begin
		rom[0] = 32'h00000013; // nop (fill)
		rom[1] = 32'h00000013; // nop (fill)
		rom[2] = 32'h00000013; // nop (fill)
		rom[3] = 32'h00000013; // nop (fill)
		rom[4] = 32'h000010B7; // lui   x1, 1
		rom[5] = 32'h38808093; // addi  x1, x1, 904   (x1 = 5000)
		rom[6] = 32'hFFF0B193; // sltiu x3, x1, -1    (want x3 = 1)
		rom[7] = 32'h00302023; // sw    x3, 0(x0)
		for (k = 8; k < 64; k = k + 1)
			rom[k] = 32'h00000013; // nop sled
	end

	// Combinational ROM read; out-of-range decodes as nop.
	always @(*) begin
		if (iaddr[31:8] == 24'b0)
			idata = rom[iaddr[7:2]];
		else
			idata = 32'h00000013;
	end

	int unsigned cyc = 0;
	always @(posedge clk) cyc <= cyc + 1;

	int seed_val = 0;

	reg seen = 0;
	reg [31:0] got_addr = 32'hFFFFFFFF;
	reg [31:0] got_data = 32'hFFFFFFFF;

	// Sample mid-cycle so the combinational D-bus has settled.
	always @(negedge clk) begin
		if (!res && ddreq && dwr && !seen) begin
			seen <= 1;
			got_addr <= daddr;
			got_data <= datao;
			$display("- store daddr=%08x datao=%08x dlen=%0d", daddr, datao, dlen);
		end
	end

	initial begin
		$value$plusargs("seed=%d", seed_val);
		$display("- tb_darkriscv_sltiu seed %0d", seed_val);
		$display("- darkriscv sltiu: x3 must be 1 (5000 < -1 signed)");
		repeat (4) @(posedge clk);
		res = 0;
		// Wait for the sw store, then judge it. Timeout keeps a dead
		// core RED instead of hanging the board.
		wait (seen == 1 || cyc > 500);
		repeat (2) @(posedge clk);
		if (seen && got_addr == 32'b0 && got_data == 32'b1) begin
			$display("tohost: PASS");
		end else begin
			$display("tohost: FAIL 0xdead");
			if (!seen)
				$display("TRAP: darkriscv sltiu no store observed in 500 cycles");
			else
				$display("TRAP: darkriscv sltiu daddr=%08x datao=%08x want daddr=00000000 datao=00000001",
					got_addr, got_data);
		end
		$display("CYCLES=%0d", cyc);
		$finish;
	end
endmodule
